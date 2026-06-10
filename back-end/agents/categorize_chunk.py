"""Categorize an ASD/ARB chunk into a canonical PolicyCategory.

Validate-time companion to the AOAI categorize skill that runs inside the
pull-mode search skillset. Uses the **same** ``CATEGORIZE_SYSTEM_PROMPT`` from
:mod:`agents.categories` so the labels chunks get at validate time line up
exactly with the labels policy chunks got at ingest time. Drift between the
two would silently sabotage the category-filter on the search call.

Authentication is via DefaultAzureCredential (adaptive picker). No API keys.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

# Module-top import (matches embeddings.py) so the openai module locks are
# taken on the main thread at import — prevents the deadlock that fires when
# categorize_chunk + embed_text run in parallel executor threads on the first
# request. We also preload the chat-submodule explicitly because openai's lazy
# resource registration still grabs a per-submodule lock on the first call.
from openai import AzureOpenAI
import openai.resources.chat  # noqa: F401  preload — race with embeddings on first parallel call

from .categories import CATEGORIZE_SYSTEM_PROMPT, PolicyCategory, parse_category
from .validate_agent import _build_credential

log = logging.getLogger(__name__)

_CLIENT_CACHE: dict[str, Any] = {}
_CLIENT_LOCK = threading.Lock()


def _get_aoai_client(endpoint: str) -> Any:
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


def categorize_chunk(text: str) -> PolicyCategory:
    """Return the PolicyCategory the AOAI chat model assigns to ``text``.

    Falls back to :attr:`PolicyCategory.GENERAL` if the model output cannot
    be parsed into the enum, so the orchestrator can still proceed with a
    well-formed search filter.
    """
    if not text or not text.strip():
        return PolicyCategory.GENERAL

    endpoint = os.getenv("FOUNDRY_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError(
            "FOUNDRY_ENDPOINT not set; required for categorization."
        )
    deployment = os.getenv("FOUNDRY_MODEL_DEPLOYMENT", "").strip()
    if not deployment:
        raise RuntimeError(
            "FOUNDRY_MODEL_DEPLOYMENT not set; required for categorization."
        )

    client = _get_aoai_client(endpoint)
    # gpt-5 family rejects the legacy ``max_tokens`` parameter (needs
    # ``max_completion_tokens``) AND rejects non-default temperature. The
    # CATEGORIZE_SYSTEM_PROMPT is explicit enough that the model picks a
    # category reliably at default temperature.
    try:
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": CATEGORIZE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            max_completion_tokens=24,
        )
    except TypeError:
        # Older openai SDK before max_completion_tokens was added.
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": CATEGORIZE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            max_tokens=24,
        )
    raw = (resp.choices[0].message.content or "").strip()
    cat = parse_category(raw)
    if cat is PolicyCategory.GENERAL and raw and raw.lower() != "general":
        log.warning("Unparseable categorize response %r — defaulting to GENERAL", raw)
    return cat
