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


# Characters stripped from the start/end of a raw model response before parsing.
# Covers common malformed outputs: leading bullets, surrounding quotes, trailing
# punctuation, stray whitespace.
_STRIP_CHARS = " \t\n\r-*•·\"'`.,:;!?()[]{}<>"


def _strip_to_category(raw: str) -> str:
    """Best-effort cleanup of a raw AOAI categorize response.

    Handles model outputs that wrap or decorate the category name:

    * leading/trailing whitespace
    * leading bullet, dash, or asterisk
    * surrounding single/double/back quotes
    * trailing punctuation (``.``, ``,``, ``;``, ``!``, ``?``)
    * JSON envelopes like ``{"category": "Network"}`` or ``["Network"]``
    * multi-line responses where only the first line is the answer

    Returns the cleaned string, empty if cleanup leaves nothing.
    """
    if not raw:
        return ""
    s = raw.strip()
    # Drop everything after the first newline — the answer should be one line.
    if "\n" in s:
        s = s.split("\n", 1)[0].strip()
    # Try JSON envelope first; fall through to char-strip if it doesn't parse.
    if s.startswith(("{", "[")):
        import json
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                # Common shapes: {"category": "X"}, {"value": "X"}, single-key.
                for key in ("category", "value", "label", "name"):
                    if key in obj and isinstance(obj[key], str):
                        s = obj[key]
                        break
                else:
                    if len(obj) == 1:
                        only = next(iter(obj.values()))
                        if isinstance(only, str):
                            s = only
            elif isinstance(obj, list) and obj and isinstance(obj[0], str):
                s = obj[0]
        except (ValueError, TypeError):
            pass
    return s.strip(_STRIP_CHARS).strip()


def _get_aoai_client(endpoint: str) -> Any:
    from .auth import credential_cache_id

    cred = _build_credential()
    cache_key = f"{endpoint}::{credential_cache_id(cred)}"
    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    with _CLIENT_LOCK:
        cached = _CLIENT_CACHE.get(cache_key)
        if cached is not None:
            return cached

        def _token_provider() -> str:
            return _build_credential().get_token(
                "https://cognitiveservices.azure.com/.default"
            ).token

        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_version="2024-10-21",
            azure_ad_token_provider=_token_provider,
        )
        _CLIENT_CACHE[cache_key] = client
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
    # Allow a dedicated lighter deployment for the per-chunk categorize call
    # (e.g. gpt-5.4-mini) so we don't pay reasoning-model latency on a label
    # picking task. Falls back to the main agent deployment when unset.
    deployment = (
        os.getenv("FOUNDRY_CATEGORIZE_DEPLOYMENT", "").strip()
        or os.getenv("FOUNDRY_MODEL_DEPLOYMENT", "").strip()
    )
    if not deployment:
        raise RuntimeError(
            "FOUNDRY_CATEGORIZE_DEPLOYMENT or FOUNDRY_MODEL_DEPLOYMENT must be "
            "set; required for categorization."
        )

    client = _get_aoai_client(endpoint)
    # gpt-5 family rejects the legacy ``max_tokens`` parameter (needs
    # ``max_completion_tokens``) AND rejects non-default temperature. The
    # CATEGORIZE_SYSTEM_PROMPT is explicit enough that the model picks a
    # category reliably at default temperature. The 64-token budget gives the
    # gpt-5 reasoning family room for a tiny reasoning pre-token sequence on
    # top of the one-name answer.
    try:
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": CATEGORIZE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            max_completion_tokens=64,
        )
    except TypeError:
        # Older openai SDK before max_completion_tokens was added.
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": CATEGORIZE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            max_tokens=64,
        )
    raw = (resp.choices[0].message.content or "").strip()
    cleaned = _strip_to_category(raw)
    cat = parse_category(cleaned)
    if cat is PolicyCategory.GENERAL and cleaned and cleaned.lower() != "general":
        log.warning(
            "Unparseable categorize response %r (cleaned=%r) — defaulting to GENERAL",
            raw, cleaned,
        )
    return cat
