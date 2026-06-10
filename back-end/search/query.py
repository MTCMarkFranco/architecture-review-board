"""Hybrid + semantic search wrapper used by agents/validate_agent.py."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from azure.identity import AzureCliCredential, DefaultAzureCredential

log = logging.getLogger("arb.search.query")

_CREDENTIAL: DefaultAzureCredential | None = None
_CLIENTS: dict[tuple[str, str], Any] = {}
_CACHE_LOCK = threading.RLock()


def _running_in_azure_host() -> bool:
    """Best-effort detection for Azure-hosted runtimes with managed identity."""
    return any(
        os.getenv(name)
        for name in (
            "IDENTITY_ENDPOINT",
            "MSI_ENDPOINT",
            "IMDS_ENDPOINT",
            "WEBSITE_INSTANCE_ID",
        )
    )


def _get_credential() -> DefaultAzureCredential:
    global _CREDENTIAL
    if _CREDENTIAL is not None:
        return _CREDENTIAL
    with _CACHE_LOCK:
        if _CREDENTIAL is None:
            if not _running_in_azure_host():
                tenant_id = os.getenv("AZURE_TENANT_ID") or None
                _CREDENTIAL = AzureCliCredential(tenant_id=tenant_id)
            else:
                _CREDENTIAL = DefaultAzureCredential()
    return _CREDENTIAL


def _get_client(endpoint: str, index: str):
    key = (endpoint, index)
    cached = _CLIENTS.get(key)
    if cached is not None:
        return cached
    from azure.search.documents import SearchClient

    with _CACHE_LOCK:
        cached = _CLIENTS.get(key)
        if cached is not None:
            return cached
        client = SearchClient(
            endpoint=endpoint,
            index_name=index,
            credential=_get_credential(),
        )
        _CLIENTS[key] = client
        return client


def search_policies(query: str, category: str | None = None,
                    source_doc: str | None = None, top: int = 8,
                    vector: list[float] | None = None) -> list[dict[str, Any]]:
    """Run hybrid (keyword + optional vector) + semantic ranking against the index.

    Returns a list of `{"header", "content", "category", "source_doc", "@score"}`.
    """
    from azure.search.documents.models import VectorizedQuery

    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    index = os.getenv("AZURE_SEARCH_INDEX", "arb-policies")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")

    client = _get_client(endpoint, index)

    filters: list[str] = []
    if category:
        safe = category.replace("'", "''")
        filters.append(f"category eq '{safe}'")
    if source_doc:
        safe = source_doc.replace("'", "''")
        filters.append(f"source_doc eq '{safe}'")
    filter_expr = " and ".join(filters) if filters else None

    vector_queries = None
    if vector:
        vector_queries = [VectorizedQuery(
            vector=vector,
            k_nearest_neighbors=top,
            fields="contentVector",
        )]

    results = client.search(
        search_text=query or "*",
        select=["id", "header", "content", "category", "source_doc"],
        filter=filter_expr,
        query_type="semantic",
        semantic_configuration_name="arb-semantic",
        vector_queries=vector_queries,
        top=top,
    )
    out: list[dict[str, Any]] = []
    for r in results:
        out.append({
            "id": r.get("id"),
            "header": r.get("header"),
            "content": r.get("content"),
            "category": r.get("category"),
            "source_doc": r.get("source_doc"),
            "@score": r.get("@search.score"),
            "@rerank": r.get("@search.reranker_score"),
        })
    return out


def get_policy_by_id(policy_id: str) -> dict[str, Any] | None:
    """Fetch a single policy document by its index key.

    Returns ``None`` if the key is not found. Used by the MCP resources
    layer (``mcp_server.resources``) to expose ``arb://policies/{id}``.
    """
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    index = os.getenv("AZURE_SEARCH_INDEX", "arb-policies")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    client = _get_client(endpoint, index)
    try:
        doc = client.get_document(key=policy_id)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "not found" in msg or "404" in msg:
            return None
        raise
    return {
        "id": doc.get("id") or policy_id,
        "header": doc.get("header"),
        "content": doc.get("content"),
        "category": doc.get("category"),
        "source_doc": doc.get("source_doc"),
    }
