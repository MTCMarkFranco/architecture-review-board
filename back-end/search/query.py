"""Hybrid + semantic search wrapper used by agents/validate_agent.py."""

from __future__ import annotations

import logging
import os
from typing import Any

from azure.identity import DefaultAzureCredential

log = logging.getLogger("arb.search.query")


def search_policies(query: str, category: str | None = None,
                    source_doc: str | None = None, top: int = 8,
                    vector: list[float] | None = None) -> list[dict[str, Any]]:
    """Run hybrid (keyword + optional vector) + semantic ranking against the index.

    Returns a list of `{"header", "content", "category", "source_doc", "@score"}`.
    """
    from azure.search.documents import SearchClient
    from azure.search.documents.models import VectorizedQuery

    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    index = os.getenv("AZURE_SEARCH_INDEX", "arb-policies")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")

    client = SearchClient(endpoint=endpoint, index_name=index,
                          credential=DefaultAzureCredential())

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
