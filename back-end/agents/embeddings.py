"""Embeddings client used by validate-time retrieval.

Wraps the Foundry text-embedding-3-large deployment via the Azure OpenAI
client. The vector returned is 3072 dimensions (native), matching the
``contentVector`` field in the ``arb-policies`` index — so we can do filtered
hybrid + semantic-ranked search with the embedding passed as ``vector_queries``.

Authentication is via DefaultAzureCredential (adaptive picker; AzureCliCredential
locally, full DAC in Azure-hosted runtimes). No API keys.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

# Module-top import (not inside the cached factory) so the openai package's
# module-level locks are taken on the main thread at import time — eliminates
# the deadlock we hit when categorize_chunk + embed_text run in parallel
# executor threads and both try to first-time-import openai concurrently.
# We also preload the embeddings sub-resource for the same reason.
from openai import AzureOpenAI
import openai.resources.embeddings  # noqa: F401  preload — race with categorize on first parallel call

from .validate_agent import _build_credential

log = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 3072  # text-embedding-3-large native

_CLIENT_CACHE: dict[str, Any] = {}
_CLIENT_LOCK = threading.Lock()


def _get_aoai_client(endpoint: str) -> Any:
    """Lazy + cached AzureOpenAI client. Thread-safe."""
    cached = _CLIENT_CACHE.get(endpoint)
    if cached is not None:
        return cached
    with _CLIENT_LOCK:
        cached = _CLIENT_CACHE.get(endpoint)
        if cached is not None:
            return cached
        token = _build_credential().get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_version="2024-10-21",
            azure_ad_token=token,
        )
        _CLIENT_CACHE[endpoint] = client
        return client


def embed_text(text: str) -> list[float]:
    """Return the 3072-dim embedding for ``text``.

    Empty/whitespace input returns a zero vector (lets callers feed empty
    chunks through the search path without an extra branch).
    """
    if not text or not text.strip():
        return [0.0] * EMBEDDING_DIMENSIONS

    endpoint = os.getenv("FOUNDRY_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError(
            "FOUNDRY_ENDPOINT not set; required for embeddings."
        )
    deployment = os.getenv("FOUNDRY_EMBEDDINGS_DEPLOYMENT", "text-embedding-3-large")

    client = _get_aoai_client(endpoint)
    resp = client.embeddings.create(model=deployment, input=[text])
    vec = resp.data[0].embedding
    if len(vec) != EMBEDDING_DIMENSIONS:
        raise RuntimeError(
            f"Embedding dimensions mismatch: got {len(vec)}, expected "
            f"{EMBEDDING_DIMENSIONS}. Index field 'contentVector' is "
            f"{EMBEDDING_DIMENSIONS}-dim — re-deploying the embedding model "
            f"with a different native size requires reindexing."
        )
    return vec
