"""Provision + run the arb-policies pull-mode indexer pipeline.

Creates or updates (idempotent):

* Azure AI Search **data source** — points the indexer at the source blob
  container; auth via the search service's system-assigned managed identity.
* Azure AI Search **skillset** — Content Understanding (crack + chunk) →
  Azure OpenAI chat completion (categorize) → Azure OpenAI embeddings; plus
  index projections that emit one index document per chunk.
* Azure AI Search **indexer** — glues data source → skillset → existing
  ``arb-policies`` index.

Optional flags trigger an indexer run and tail status to completion, and/or
purge the index of stale chunks before running.

Authentication is via :class:`DefaultAzureCredential` (with the project's
adaptive picker — ``AzureCliCredential`` locally, full DAC in Azure-hosted
runtimes). No API keys.

Required env vars (set via the repo-root ``.env``):

  AZURE_SEARCH_ENDPOINT          https://arb-search-cc.search.windows.net
  AZURE_SEARCH_INDEX             arb-policies (default)
  FOUNDRY_ENDPOINT               https://foundry-cc-canada.cognitiveservices.azure.com/
  FOUNDRY_MODEL_DEPLOYMENT       gpt-5.3-chat-1 (or your chat deployment)
  FOUNDRY_EMBEDDINGS_DEPLOYMENT  text-embedding-3-large
  FOUNDRY_CU_ENDPOINT            Optional. AI Services account URL used as the
                                 Content Understanding billing target. Defaults
                                 to FOUNDRY_ENDPOINT. Override if Canada
                                 Central does not support CU.
  STORAGE_ACCOUNT_RESOURCE_ID    /subscriptions/.../storageAccounts/<name>
  STORAGE_CONTAINER              arb-policies-source (default)

Usage:

  python -m search.build_indexer                  # provision only (idempotent)
  python -m search.build_indexer --run            # provision + trigger a run
  python -m search.build_indexer --run --purge    # purge then run (clean ingest)
  python -m search.build_indexer --status         # show last run status only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from agents.categories import CATEGORIZE_SYSTEM_PROMPT
from agents.validate_agent import _build_credential

log = logging.getLogger("build_indexer")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

SEARCH_API_VERSION = "2025-11-01-preview"
SEARCH_SCOPE = "https://search.azure.com/.default"

HERE = Path(__file__).resolve().parent
DEFAULT_CONTAINER = os.getenv("STORAGE_CONTAINER", "arb-policies-source")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        log.error("Required env var %s is not set.", name)
        sys.exit(2)
    return val


def _load_template(filename: str, substitutions: dict[str, str]) -> dict:
    """Read a JSON template and substitute ``{{KEY}}`` placeholders."""
    text = (HERE / filename).read_text(encoding="utf-8")
    for key, value in substitutions.items():
        text = text.replace("{{" + key + "}}", value)
    # Catch any unsubstituted placeholders early.
    if "{{" in text:
        # Find the first one for a clearer error message.
        start = text.find("{{")
        end = text.find("}}", start)
        token = text[start:end + 2] if end != -1 else text[start:start + 40]
        raise RuntimeError(
            f"Unsubstituted placeholder {token!r} in {filename}. "
            f"Check the env vars passed to substitutions."
        )
    return json.loads(text)


def _bearer_headers(credential) -> dict[str, str]:
    token = credential.get_token(SEARCH_SCOPE).token
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _put(session, url: str, body: dict, headers: dict[str, str], what: str) -> None:
    log.info("PUT %s", what)
    r = session.put(url, headers=headers, json=body, timeout=60)
    if r.status_code >= 400:
        log.error("PUT %s failed: %s %s", what, r.status_code, r.text)
        r.raise_for_status()
    log.info("  → %s", r.status_code)


def _post(session, url: str, headers: dict[str, str], what: str) -> None:
    log.info("POST %s", what)
    r = session.post(url, headers=headers, timeout=60)
    if r.status_code >= 400:
        log.error("POST %s failed: %s %s", what, r.status_code, r.text)
        r.raise_for_status()


def _get(session, url: str, headers: dict[str, str]) -> dict:
    r = session.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Indexer status tailing
# ---------------------------------------------------------------------------

def _tail_status(session, search_endpoint: str, indexer_name: str,
                 headers: dict[str, str], timeout_seconds: int = 1800) -> int:
    """Block until the indexer's last run reaches success or terminal error.

    Returns 0 on success, non-zero on error. Polls every 5 seconds.
    """
    url = (f"{search_endpoint}/indexers('{indexer_name}')/search.status"
           f"?api-version={SEARCH_API_VERSION}")
    start = time.monotonic()
    last_status: str | None = None
    while True:
        info = _get(session, url, headers)
        last = info.get("lastResult") or {}
        status = last.get("status") or "(no run yet)"
        if status != last_status:
            log.info("Indexer status: %s | succeeded=%s failed=%s",
                     status, last.get("itemsProcessed"), last.get("itemsFailed"))
            last_status = status
        if status == "success":
            log.info("Indexer completed: %s items processed, %s failed.",
                     last.get("itemsProcessed"), last.get("itemsFailed"))
            return 0
        if status in ("transientFailure", "persistentFailure", "error"):
            log.error("Indexer terminal error: %s",
                      last.get("errorMessage") or last.get("errors"))
            return 1
        if time.monotonic() - start > timeout_seconds:
            log.error("Timed out waiting for indexer after %ss", timeout_seconds)
            return 1
        time.sleep(5)


# ---------------------------------------------------------------------------
# Index doc purge (delegates to the legacy helper)
# ---------------------------------------------------------------------------

def _purge_index(search_endpoint: str, index_name: str, credential) -> int:
    """Delete every document currently in the index. Returns count deleted."""
    from azure.search.documents import SearchClient
    sc = SearchClient(endpoint=search_endpoint, index_name=index_name,
                      credential=credential)
    ids = [d["id"] for d in sc.search(search_text="*", select=["id"], top=10000)]
    if not ids:
        return 0
    sc.delete_documents(documents=[{"id": i} for i in ids])
    return len(ids)


# ---------------------------------------------------------------------------
# Main provisioning flow
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true",
                        help="Trigger the indexer after provisioning and tail status.")
    parser.add_argument("--purge", action="store_true",
                        help="Delete all index documents before running. Use when "
                             "replacing the source blob so stale chunks are removed.")
    parser.add_argument("--status", action="store_true",
                        help="Just print the indexer's last-run status and exit.")
    args = parser.parse_args()

    search_endpoint = _require_env("AZURE_SEARCH_ENDPOINT").rstrip("/")
    index_name = os.getenv("AZURE_SEARCH_INDEX", "arb-policies")

    try:
        import requests
    except ImportError as e:  # pragma: no cover
        log.error("`requests` is required: pip install -r requirements.txt (%s)", e)
        return 2

    credential = _build_credential()
    headers = _bearer_headers(credential)
    session = requests.Session()

    if args.status:
        return _tail_status(session, search_endpoint, "arb-policies-indexer", headers,
                            timeout_seconds=10)

    foundry_endpoint = _require_env("FOUNDRY_ENDPOINT")
    if not foundry_endpoint.endswith("/"):
        foundry_endpoint += "/"
    cu_endpoint = os.getenv("FOUNDRY_CU_ENDPOINT", foundry_endpoint)
    if not cu_endpoint.endswith("/"):
        cu_endpoint += "/"

    substitutions = {
        "AZURE_SEARCH_INDEX": index_name,
        "FOUNDRY_ENDPOINT": foundry_endpoint,
        "FOUNDRY_CU_ENDPOINT": cu_endpoint,
        "FOUNDRY_MODEL_DEPLOYMENT": _require_env("FOUNDRY_MODEL_DEPLOYMENT"),
        "FOUNDRY_EMBEDDINGS_DEPLOYMENT": _require_env("FOUNDRY_EMBEDDINGS_DEPLOYMENT"),
        "COGNITIVE_SERVICES_KEY": _require_env("COGNITIVE_SERVICES_KEY"),
        "STORAGE_ACCOUNT_RESOURCE_ID": _require_env("STORAGE_ACCOUNT_RESOURCE_ID"),
        "STORAGE_CONTAINER": DEFAULT_CONTAINER,
        # The categorize prompt is JSON-escaped so it survives substitution
        # into a JSON document. The skillset definition wraps the placeholder
        # in single quotes as part of an OData literal expression.
        "CATEGORIZE_SYSTEM_PROMPT": json.dumps(CATEGORIZE_SYSTEM_PROMPT)[1:-1].replace("'", "''"),
    }

    datasource = _load_template("datasource_definition.json", substitutions)
    skillset = _load_template("skillset_definition.json", substitutions)
    indexer = _load_template("indexer_definition.json", substitutions)
    index = _load_template("index_schema.json", substitutions)

    api = f"?api-version={SEARCH_API_VERSION}"
    _put(session, f"{search_endpoint}/datasources('arb-policies-blob'){api}",
         datasource, headers, "datasource arb-policies-blob")
    _put(session, f"{search_endpoint}/indexes('{index_name}'){api}",
         index, headers, f"index {index_name}")
    _put(session, f"{search_endpoint}/skillsets('arb-policies-skillset'){api}",
         skillset, headers, "skillset arb-policies-skillset")
    _put(session, f"{search_endpoint}/indexers('arb-policies-indexer'){api}",
         indexer, headers, "indexer arb-policies-indexer")

    if args.purge:
        purged = _purge_index(search_endpoint, index_name, credential)
        log.info("Purged %d existing documents from index '%s'", purged, index_name)

    if args.run:
        _post(session, f"{search_endpoint}/indexers('arb-policies-indexer')/search.run{api}",
              headers, "trigger indexer run")
        return _tail_status(session, search_endpoint, "arb-policies-indexer", headers)

    log.info("Provisioning complete. Re-run with --run to trigger an ingest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
