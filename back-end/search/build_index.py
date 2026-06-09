"""Build (create-or-update) the Azure AI Search index and ingest policies.

Reads `index_schema.json`, creates an index in the configured Search service
using DefaultAzureCredential, then ingests documents:
  - From the docx produced by build_azure_policies.py (preferred), OR
  - From back-end/file_processing/data/policies.json (legacy fallback).

Generates embeddings using a Foundry-hosted text embedding model.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from azure.identity import DefaultAzureCredential

from .categorize import derive_category

log = logging.getLogger("build_index")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = Path(__file__).resolve().parent / "index_schema.json"
LEGACY_POLICIES = REPO_ROOT / "back-end" / "file_processing" / "data" / "policies.json"
DEFAULT_DOCX = REPO_ROOT / "back-end" / "file_processing" / "data" / "azure_policies.docx"

CHUNK_SIZE = 3500  # characters
EMBEDDING_DIMENSIONS = 1536


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip()).strip("-").lower()
    return s or "policy"


def chunk(text: str, size: int = CHUNK_SIZE) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [text[i:i + size] for i in range(0, len(text), size)]


# -- Sources ---------------------------------------------------------------

def policies_from_docx(path: Path) -> list[dict]:
    from docx import Document  # local import so the script is usable without docx
    doc = Document(str(path))
    items: list[dict] = []
    cur_header: str | None = None
    buf: list[str] = []
    for p in doc.paragraphs:
        line = (p.text or "").strip()
        if not line:
            continue
        is_section = (line.isupper()
                      and "INTERNAL" not in line
                      and line[:1].isdigit())
        if is_section:
            if cur_header is not None:
                items.append({"header": cur_header, "content": " ".join(buf).strip()})
            cur_header = line
            buf = []
        elif cur_header:
            buf.append(line)
    if cur_header is not None:
        items.append({"header": cur_header, "content": " ".join(buf).strip()})
    return items


def policies_from_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def policies_from_pdf(path: Path) -> list[dict]:
    """Extract policy sections from a PDF using the shared header rule.

    Delegates to :func:`file_processing.parsing.extract_policies` so the
    PDF and DOCX paths share the exact same section-detection contract
    (UPPERCASE, no 'INTERNAL', leading digit).
    """
    # Allow running this module either as a package (``python -m search.build_index``)
    # or as a script from the back-end directory.
    try:
        from file_processing.parsing import extract_policies  # type: ignore
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(REPO_ROOT / "back-end"))
        from file_processing.parsing import extract_policies  # type: ignore
    return extract_policies(str(path))


# -- Embeddings ------------------------------------------------------------

def make_embedder(endpoint: str, deployment: str):
    try:
        from openai import AzureOpenAI
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "openai package is required for embeddings (transitive of azure-ai-projects). "
            "Install requirements.txt."
        ) from e
    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_version="2024-10-21",
        azure_ad_token=token,
    )

    def embed(text: str) -> list[float]:
        if not text.strip():
            return [0.0] * EMBEDDING_DIMENSIONS
        resp = client.embeddings.create(
            model=deployment,
            input=[text],
            dimensions=EMBEDDING_DIMENSIONS,
        )
        vec = resp.data[0].embedding
        if len(vec) != EMBEDDING_DIMENSIONS:
            raise RuntimeError(
                f"Embedding dimensions mismatch: got {len(vec)}, expected "
                f"{EMBEDDING_DIMENSIONS}. Override EMBEDDING_DIMENSIONS in build_index.py."
            )
        return vec

    return embed


# -- Index management ------------------------------------------------------

def build_index_objects(schema: dict, name: str):
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SemanticConfiguration,
        SemanticField,
        SemanticPrioritizedFields,
        SemanticSearch,
        VectorSearch,
        VectorSearchProfile,
    )

    type_map = {
        "Edm.String": SearchFieldDataType.String,
        "Edm.Int32": SearchFieldDataType.Int32,
        "Collection(Edm.Single)": SearchFieldDataType.Collection(SearchFieldDataType.Single),
    }

    fields = []
    for f in schema["fields"]:
        kw = {
            "name": f["name"],
            "type": type_map[f["type"]],
            "key": f.get("key", False),
            "filterable": f.get("filterable", False),
            "sortable": f.get("sortable", False),
            "facetable": f.get("facetable", False),
            "searchable": f.get("searchable", False),
        }
        if "analyzer" in f:
            kw["analyzer_name"] = f["analyzer"]
        if "dimensions" in f:
            kw["vector_search_dimensions"] = f["dimensions"]
            kw["vector_search_profile_name"] = f["vectorSearchProfile"]
        fields.append(SearchField(**kw))

    vs = schema["vectorSearch"]
    vector_search = VectorSearch(
        profiles=[VectorSearchProfile(
            name=p["name"], algorithm_configuration_name=p["algorithm"]
        ) for p in vs["profiles"]],
        algorithms=[HnswAlgorithmConfiguration(name=a["name"]) for a in vs["algorithms"]],
    )

    sem = schema["semantic"]["configurations"][0]
    pf = sem["prioritizedFields"]
    semantic = SemanticSearch(configurations=[SemanticConfiguration(
        name=sem["name"],
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name=pf["titleField"]["fieldName"]),
            content_fields=[SemanticField(field_name=c["fieldName"])
                            for c in pf["prioritizedContentFields"]],
            keywords_fields=[SemanticField(field_name=k["fieldName"])
                             for k in pf["prioritizedKeywordsFields"]],
        ),
    )])

    return SearchIndex(name=name, fields=fields, vector_search=vector_search,
                       semantic_search=semantic)


def create_or_update_index(endpoint: str, name: str) -> None:
    from azure.search.documents.indexes import SearchIndexClient
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    idx = build_index_objects(schema, name)
    client = SearchIndexClient(endpoint=endpoint, credential=DefaultAzureCredential())
    client.create_or_update_index(idx)
    log.info("Index '%s' created/updated.", name)


def purge_index_documents(endpoint: str, name: str) -> int:
    """Delete every document currently in the index. Returns count deleted.

    The index schema, vector profiles, and semantic config are untouched.
    Use before re-ingesting a replacement policy document so stale chunks
    from the previous source do not pollute search results.
    """
    from azure.search.documents import SearchClient
    sc = SearchClient(endpoint=endpoint, index_name=name,
                      credential=DefaultAzureCredential())
    ids = [d["id"] for d in sc.search(search_text="*", select=["id"], top=10000)]
    if not ids:
        return 0
    sc.delete_documents(documents=[{"id": i} for i in ids])
    return len(ids)


def ingest_documents(endpoint: str, name: str, policies: Iterable[dict],
                     source_doc: str, embed) -> int:
    from azure.search.documents import SearchClient
    sc = SearchClient(endpoint=endpoint, index_name=name,
                      credential=DefaultAzureCredential())
    docs: list[dict] = []
    seen_ids: set[str] = set()
    for entry in policies:
        header = entry["header"]
        content = entry.get("content", "")
        if not content.strip():
            log.info("Skipping empty section %r", header)
            continue
        category = derive_category(header)
        chunks = chunk(content)
        for i, c in enumerate(chunks):
            base = _slug(header) + (f"-{i}" if i else "")
            doc_id = base
            n = 1
            while doc_id in seen_ids:
                n += 1
                doc_id = f"{base}-{n}"
            seen_ids.add(doc_id)
            docs.append({
                "id": doc_id,
                "header": header,
                "content": c,
                "contentVector": embed(c),
                "category": category,
                "source_doc": source_doc,
                "chunk_index": i,
            })
    log.info("Uploading %d documents to index '%s'", len(docs), name)
    results = sc.upload_documents(documents=docs)
    failed = [r for r in results if not r.succeeded]
    if failed:
        for r in failed[:5]:
            log.error("Upload failed: %s %s", r.key, r.error_message)
        raise RuntimeError(f"{len(failed)}/{len(results)} document upload(s) failed")
    return len(docs)


# -- CLI -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-name", default=os.getenv("AZURE_SEARCH_INDEX", "arb-policies"))
    parser.add_argument("--search-endpoint",
                        default=os.getenv("AZURE_SEARCH_ENDPOINT", ""))
    parser.add_argument("--foundry-endpoint",
                        default=os.getenv("FOUNDRY_ENDPOINT", ""))
    parser.add_argument("--embeddings-deployment",
                        default=os.getenv("FOUNDRY_EMBEDDINGS_DEPLOYMENT", "text-embedding-3-large"))
    parser.add_argument("--pdf", type=Path, default=None,
                        help="Source policy PDF (highest priority). Use this for "
                             "customer-supplied policy documents.")
    parser.add_argument("--docx", type=Path, default=DEFAULT_DOCX,
                        help="Source docx. Used when --pdf is not provided.")
    parser.add_argument("--policies-json", type=Path, default=LEGACY_POLICIES,
                        help="Legacy JSON fallback.")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Only create/update the index.")
    parser.add_argument("--purge", action="store_true",
                        help="Delete all existing documents in the index before "
                             "ingesting. Use when replacing the policy document so "
                             "stale chunks from the previous doc are removed.")
    args = parser.parse_args()

    log.info(
        "embeddings model=%s requested_dims=%d",
        args.embeddings_deployment,
        EMBEDDING_DIMENSIONS,
    )

    if not args.search_endpoint:
        log.error("AZURE_SEARCH_ENDPOINT not set.")
        return 2
    create_or_update_index(args.search_endpoint, args.index_name)
    if args.skip_ingest:
        return 0

    if args.pdf is not None:
        if not args.pdf.exists():
            log.error("PDF not found: %s", args.pdf)
            return 2
        policies = policies_from_pdf(args.pdf)
        source_doc = args.pdf.name
    elif args.docx.exists():
        policies = policies_from_docx(args.docx)
        source_doc = args.docx.name
    elif args.policies_json.exists():
        policies = policies_from_json(args.policies_json)
        source_doc = args.policies_json.name
    else:
        log.error("No policy source available. Pass --pdf, generate %s, or "
                  "provide %s.", DEFAULT_DOCX, LEGACY_POLICIES)
        return 2

    if not policies:
        log.error("Source %r yielded no policy sections. Check the document's "
                  "headers match the contract: UPPERCASE, leading digit, no "
                  "'INTERNAL'.", source_doc)
        return 2
    log.info("Loaded %d policy sections from %s", len(policies), source_doc)

    if args.purge:
        purged = purge_index_documents(args.search_endpoint, args.index_name)
        log.info("Purged %d existing documents from index '%s'",
                 purged, args.index_name)

    if not args.foundry_endpoint:
        log.error("FOUNDRY_ENDPOINT not set; cannot embed.")
        return 2
    embed = make_embedder(args.foundry_endpoint, args.embeddings_deployment)

    n = ingest_documents(args.search_endpoint, args.index_name,
                         policies, source_doc, embed)
    log.info("Ingested %d documents.", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
