"""Policy ingestion test suite.

Tiers:
  - smoke (no marker): builds the policies docx via ``build_azure_policies``
    and verifies ``extract_policies_docx`` returns sections matching the
    canonical header contract. Runs without Azure credentials.
  - integration (``@pytest.mark.integration``): exercises the pull-mode
    pipeline end-to-end (uploads the docx to the source blob container,
    triggers ``arb-policies-indexer``, waits for it to drain, asserts the
    expected category coverage in the ``arb-policies`` index). Skipped when
    Azure env vars are missing.

The legacy push-mode ingest (``search/build_index.py``) was deleted in #61
when the pull-mode pipeline replaced it; its smoke tests went with it.
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

def test_policies_docx_sections_match_header_contract():
    """Build the docx, re-extract, assert every section matches the contract."""
    try:
        from file_processing.build_azure_policies import build  # type: ignore
        from file_processing.parsing import extract_policies_docx  # type: ignore
    except ImportError as e:
        pytest.skip(f"build_azure_policies / parsing not importable: {e}")

    docx_path = build()
    assert Path(docx_path).exists(), f"Builder did not produce {docx_path}"

    sections = extract_policies_docx(str(docx_path))
    assert sections, "Extractor returned zero sections"
    for s in sections:
        assert s["header"].isupper(), f"Header not uppercase: {s['header']!r}"
        assert s["header"][:1].isdigit(), f"Header not numeric-prefixed: {s['header']!r}"
        assert s["content"].strip(), f"Empty content for {s['header']!r}"


# ---------------------------------------------------------------------------
# Integration tier — pull-mode pipeline end-to-end.
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_pull_indexer_end_to_end():
    """Upload the canonical docx to the source container, run the indexer,
    assert category coverage in the live arb-policies index.

    Requires: AZURE_SEARCH_ENDPOINT, STORAGE_ACCOUNT_RESOURCE_ID, STORAGE_CONTAINER,
    FOUNDRY_ENDPOINT, FOUNDRY_MODEL_DEPLOYMENT, FOUNDRY_EMBEDDINGS_DEPLOYMENT.
    """
    require_env(
        "AZURE_SEARCH_ENDPOINT",
        "STORAGE_ACCOUNT_RESOURCE_ID",
        "FOUNDRY_ENDPOINT",
        "FOUNDRY_MODEL_DEPLOYMENT",
        "FOUNDRY_EMBEDDINGS_DEPLOYMENT",
    )

    try:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents import SearchClient
        from azure.storage.blob import BlobServiceClient
    except ImportError as e:  # pragma: no cover
        pytest.skip(f"Azure SDKs not installed: {e}")

    try:
        from file_processing.build_azure_policies import build  # type: ignore
        from search.build_indexer import main as run_indexer_cli  # type: ignore
    except ImportError as e:
        pytest.skip(f"Pipeline modules not importable: {e}")

    docx_path = Path(build())

    storage_id = os.environ["STORAGE_ACCOUNT_RESOURCE_ID"]
    storage_name = storage_id.rsplit("/", 1)[-1]
    container = os.getenv("STORAGE_CONTAINER", "arb-policies-source")
    cred = DefaultAzureCredential()

    blob_name = f"test-pull-{uuid.uuid4().hex[:6]}-{docx_path.name}"
    bsc = BlobServiceClient(
        account_url=f"https://{storage_name}.blob.core.windows.net",
        credential=cred,
    )
    container_client = bsc.get_container_client(container)
    with docx_path.open("rb") as f:
        container_client.upload_blob(name=blob_name, data=f, overwrite=True)

    try:
        # Provision (idempotent) + trigger run + tail.
        import sys as _sys
        prev_argv = _sys.argv
        try:
            _sys.argv = ["build_indexer", "--run"]
            rc = run_indexer_cli()
        finally:
            _sys.argv = prev_argv
        assert rc == 0, "Indexer run did not complete successfully"

        sc = SearchClient(
            endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
            index_name=os.getenv("AZURE_SEARCH_INDEX", "arb-policies"),
            credential=cred,
        )
        # Allow a few seconds for the index to settle.
        deadline = time.time() + 60
        hits: list = []
        while time.time() < deadline:
            hits = list(sc.search(
                search_text="*",
                filter=f"source_doc eq '{blob_name}'",
                top=5,
            ))
            if hits:
                break
            time.sleep(3)
        assert hits, "No chunks projected from the uploaded blob"
        for h in hits:
            cat = h.get("category")
            assert cat, f"Chunk {h.get('id')!r} has empty category"
    finally:
        try:
            container_client.delete_blob(blob_name)
        except Exception:  # pragma: no cover - best-effort teardown
            pass

