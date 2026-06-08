"""Policy ingestion test suite (issue #18).

Tiers:
  - smoke (no marker): builds the policies docx via ``build_azure_policies``
    and verifies ``extract_policies_docx`` returns 15 sections. Runs without
    Azure credentials and without the search refactor (#24) merged.
  - integration (``@pytest.mark.integration``): exercises the live Azure AI
    Search pipeline against a throwaway index using DefaultAzureCredential.
    Skipped when env vars are missing or when the ``search`` package from
    PR #24 has not been merged onto this branch.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import require_env  # noqa: F401  (path setup side effect)


# ---------------------------------------------------------------------------
# Smoke tier — no Azure, no sibling-PR dependencies.
# ---------------------------------------------------------------------------

def test_policies_docx_has_fifteen_sections():
    """Build the docx, re-extract, assert the 15-section invariant."""
    try:
        from file_processing.build_azure_policies import build  # type: ignore
        from file_processing.parsing import extract_policies_docx  # type: ignore
    except ImportError as e:
        pytest.skip(f"Sibling PR #17 not yet merged onto this branch: {e}")

    docx_path = build()
    assert Path(docx_path).exists(), f"Builder did not produce {docx_path}"

    sections = extract_policies_docx(str(docx_path))
    assert len(sections) == 15, f"Expected 15 sections, got {len(sections)}"
    for s in sections:
        assert s["header"].isupper(), f"Header not uppercase: {s['header']!r}"
        assert s["header"][:1].isdigit(), f"Header not numeric-prefixed: {s['header']!r}"
        assert s["content"].strip(), f"Empty content for {s['header']!r}"


# ---------------------------------------------------------------------------
# Integration tier — live Azure AI Search + Foundry embeddings.
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_policy_ingest_end_to_end():
    """End-to-end ingestion against a throwaway index in Canada Central."""
    require_env("AZURE_SEARCH_ENDPOINT", "FOUNDRY_ENDPOINT")

    try:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents import SearchClient
        from azure.search.documents.indexes import SearchIndexClient
    except ImportError as e:  # pragma: no cover
        pytest.skip(f"azure-search-documents not installed: {e}")

    try:
        from file_processing.build_azure_policies import build  # type: ignore
        from file_processing.parsing import extract_policies_docx  # type: ignore
    except ImportError as e:
        pytest.skip(f"Sibling PR #17 not merged: {e}")

    try:
        from search.build_index import (  # type: ignore
            create_or_update_index,
            ingest_documents,
            make_embedder,
        )
        from search.categorize import derive_category  # type: ignore
    except ImportError as e:
        pytest.skip(f"Sibling PR #24 (search refactor) not merged: {e}")

    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    foundry = os.environ["FOUNDRY_ENDPOINT"]
    embed_deployment = os.getenv("FOUNDRY_EMBEDDINGS_DEPLOYMENT", "text-embedding-3-large")
    index_name = f"arb-test-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    cred = DefaultAzureCredential()

    try:
        # Build source doc + extract.
        docx_path = build()
        sections = extract_policies_docx(str(docx_path))
        assert len(sections) == 15

        # Create the test index.
        create_or_update_index(endpoint, index_name)

        # Verify schema shape.
        idx_client = SearchIndexClient(endpoint=endpoint, credential=cred)
        idx = idx_client.get_index(index_name)
        field_names = {f.name for f in idx.fields}
        assert {"id", "header", "content", "contentVector", "category"} <= field_names

        category_field = next(f for f in idx.fields if f.name == "category")
        assert category_field.filterable, "category must be filterable"

        vector_field = next(f for f in idx.fields if f.name == "contentVector")
        assert vector_field.vector_search_dimensions, "contentVector must be a vector field"

        assert idx.semantic_search and idx.semantic_search.configurations, \
            "Semantic configuration missing"

        # Upload 15 docs (one per section, single chunk each since content is short).
        embed = make_embedder(foundry, embed_deployment)
        n = ingest_documents(endpoint, index_name, sections, "azure_policies.docx", embed)
        assert n >= 15

        # Wait for indexing to settle.
        sc = SearchClient(endpoint=endpoint, index_name=index_name, credential=cred)
        deadline = time.time() + 60
        category_hits: list = []
        while time.time() < deadline:
            results = list(sc.search(
                search_text="*",
                filter="category eq 'Security and Governance'",
                top=5,
            ))
            if results:
                category_hits = results
                break
            time.sleep(3)

        # Derive_category mapping for header "14. COMPLIANCE GOVERNANCE AND POLICY"
        # returns "Security and Governance" — assert at least one hit.
        assert derive_category("14. COMPLIANCE GOVERNANCE AND POLICY") == "Security and Governance"
        assert category_hits, "Category filter returned no documents within timeout"

    finally:
        try:
            SearchIndexClient(endpoint=endpoint, credential=cred).delete_index(index_name)
        except Exception:  # pragma: no cover - best-effort teardown
            pass
