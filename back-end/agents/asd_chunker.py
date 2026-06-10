"""Semantic chunking for uploaded ASD/ARB documents.

Validate-time companion to the pull-mode policy ingest pipeline. Cracks an
uploaded PDF or DOCX into layout-aware Markdown via Azure AI Document
Intelligence's prebuilt-layout model, then splits the Markdown into
~3500-character chunks at heading boundaries (with 200-char overlap) so the
chunks survive downstream embedding + categorize calls.

Uses the same FOUNDRY_ENDPOINT (an AI Services multi-service account) for
DocIntel billing. No API keys — DefaultAzureCredential via the adaptive picker.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

from .validate_agent import _build_credential

log = logging.getLogger(__name__)

# Match the policy-side ingest chunker so chunk sizing is consistent end-to-end.
_MAX_CHARS = 3500
_OVERLAP_CHARS = 200
# Don't emit tiny tail chunks — merge anything smaller into the prior chunk.
_MIN_CHARS = 200

_DOC_INTEL_CLIENT_CACHE: dict[str, Any] = {}
_DOC_INTEL_LOCK = threading.Lock()


def _get_doc_intel_client(endpoint: str) -> Any:
    """Lazy + cached DocumentIntelligenceClient construction (thread-safe)."""
    cached = _DOC_INTEL_CLIENT_CACHE.get(endpoint)
    if cached is not None:
        return cached
    with _DOC_INTEL_LOCK:
        cached = _DOC_INTEL_CLIENT_CACHE.get(endpoint)
        if cached is not None:
            return cached
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "azure-ai-documentintelligence is required for ASD chunking. "
                "Install requirements.txt."
            ) from e
        client = DocumentIntelligenceClient(
            endpoint=endpoint,
            credential=_build_credential(),
        )
        _DOC_INTEL_CLIENT_CACHE[endpoint] = client
        return client


def chunk_asd_document(file_bytes: bytes, filename: str | None = None) -> list[str]:
    """Crack a PDF/DOCX into semantic chunks.

    Returns a list of chunk text strings (markdown-formatted). Empty list when
    the input has no extractable text.

    Raises :class:`RuntimeError` when DocIntel cannot be reached / authenticated;
    the orchestrator turns this into a single deterministic ``Error`` finding so
    the user sees a clear failure rather than a silent empty result.
    """
    if not file_bytes:
        return []

    endpoint = os.getenv("FOUNDRY_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError(
            "FOUNDRY_ENDPOINT not set; required to call Azure AI Document Intelligence."
        )
    if not endpoint.endswith("/"):
        endpoint += "/"

    client = _get_doc_intel_client(endpoint)

    try:
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "azure-ai-documentintelligence is required for ASD chunking."
        ) from e

    log.info("Cracking ASD doc via DocIntel layout model (filename=%s, bytes=%d)",
             filename, len(file_bytes))
    poller = client.begin_analyze_document(
        model_id="prebuilt-layout",
        body=AnalyzeDocumentRequest(bytes_source=file_bytes),
        output_content_format="markdown",
    )
    result = poller.result()
    markdown = getattr(result, "content", "") or ""
    log.info("DocIntel returned %d markdown chars; chunking…", len(markdown))

    chunks = _split_markdown(markdown,
                             max_chars=_MAX_CHARS,
                             overlap=_OVERLAP_CHARS,
                             min_chars=_MIN_CHARS)
    log.info("Produced %d chunks (max=%d, overlap=%d, min=%d)",
             len(chunks), _MAX_CHARS, _OVERLAP_CHARS, _MIN_CHARS)
    return chunks


# ---------------------------------------------------------------------------
# Markdown header-aware splitter
# ---------------------------------------------------------------------------

# Match a markdown heading at the start of a line (any depth h1..h6).
_HEADING_RE = re.compile(r"(?m)^(#{1,6}) ")


def _split_markdown(markdown: str, max_chars: int, overlap: int,
                    min_chars: int) -> list[str]:
    """Split markdown into semantic chunks.

    Strategy:
      1. Cut on heading boundaries (h1..h6) — these are natural section starts.
      2. If a section is larger than ``max_chars``, fall back to fixed-size
         windowing within the section with ``overlap`` chars of carry-over so
         the agent sees boundary context.
      3. Merge tail chunks smaller than ``min_chars`` into the preceding chunk
         so we don't ship 30-char prompt blocks to the LLM.
    """
    text = (markdown or "").strip()
    if not text:
        return []

    # Step 1 — cut on heading boundaries while keeping the heading line with
    # the section it introduces (lookahead split).
    section_starts = [0] + [m.start() for m in _HEADING_RE.finditer(text)][1:]
    section_starts.sort()
    sections: list[str] = []
    for i, start in enumerate(section_starts):
        end = section_starts[i + 1] if i + 1 < len(section_starts) else len(text)
        sec = text[start:end].strip()
        if sec:
            sections.append(sec)
    if not sections:
        sections = [text]

    # Step 2 — window large sections.
    chunks: list[str] = []
    for sec in sections:
        if len(sec) <= max_chars:
            chunks.append(sec)
            continue
        step = max(1, max_chars - overlap)
        for i in range(0, len(sec), step):
            piece = sec[i:i + max_chars].strip()
            if piece:
                chunks.append(piece)

    # Step 3 — merge tiny tails.
    merged: list[str] = []
    for c in chunks:
        if merged and len(c) < min_chars:
            merged[-1] = (merged[-1] + "\n\n" + c).strip()
        else:
            merged.append(c)
    return merged
