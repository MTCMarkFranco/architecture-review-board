"""Validate-ARB hosted agent client.

Calls a Foundry v2 hosted prompt agent ``ValidateArbAgent`` per ASD section
and aggregates the JSON findings into a single list.

Foundry v2 hosted prompt agents are invoked through the **Responses API** via
the project's OpenAI client (``project.get_openai_client(agent_name=...)``);
the agent's ``model`` parameter is its model deployment name. This is NOT the
classic Assistants/Agents v1 surface (threads/messages/runs) — that path
requires ``asst_xxx`` ids which Foundry v2 hosted agents do not have.

Authentication is via ``DefaultAzureCredential`` (no API keys).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any

from azure.identity import AzureCliCredential, DefaultAzureCredential

from .categories import (
    ASD_SECTION_CATEGORIES,
    DEFAULT_SECTION_CATEGORIES,
    PolicyCategory,
)
from .config import Config
from .errors import AgentInvocationError, AgentNotFoundError

logger = logging.getLogger(__name__)

# Module-level cache of (project_endpoint, agent_name) → agent-scoped OpenAI
# client. The OpenAI client is thread-safe; caching avoids the per-call
# ``get_openai_client`` round-trip overhead.
_OAI_CLIENT_CACHE: dict[tuple[str, str], Any] = {}
_OAI_CLIENT_LOCK = threading.Lock()

# Backwards-compatible alias for callers / tests that still reach for
# ``SECTION_CATEGORIES`` as a plain ``dict[str, list[str]]``. New code should
# import :data:`agents.categories.ASD_SECTION_CATEGORIES` directly.
SECTION_CATEGORIES: dict[str, list[str]] = {
    section: [c.value for c in cats]
    for section, cats in ASD_SECTION_CATEGORIES.items()
}

SYSTEM_PROMPT = (
    "You are an Azure architecture reviewer. For each architecture-design-document "
    "section provided, validate it ONLY against the policies listed under "
    "[Retrieved Policies] in this prompt. Do NOT invent policies, do NOT call any "
    "tools, and do NOT cite policies that are not present in [Retrieved Policies]. "
    "Identify any issues. When you reference a policy header, replace underscores "
    "with spaces.\n\n"
    "Use these values for Type:\n"
    "- Violation: section breaks a hard policy requirement (must-fix).\n"
    "- Deviation: section goes against policy intent or preferred pattern (should-fix).\n"
    "- Suggestion: section is acceptable but could be improved per policy guidance.\n"
    "- Missing: a required topic from the policy is not addressed at all in the section.\n\n"
    "Return ONE JSON object per finding with the schema:\n"
    "{\"Type\": \"Violation|Deviation|Suggestion|Missing\", \"Issue\": \"<short title>\","
    " \"Description\": \"<detail>\", \"Principles\": \"<policy header>\","
    " \"Mandatory\": <bool>, \"Category\": \"<policy category>\"}\n"
    "If [Retrieved Policies] is empty or the section is empty/contains only 'N/A', "
    "return an empty JSON array.\n"
    "Output ONLY a JSON array of these objects, no prose."
)

# Per-policy content snippet limit inside the [Retrieved Policies] prompt block.
_POLICY_SNIPPET_CHARS = 4096
# Section text we send as the search query (full text still goes to the agent).
_SEARCH_QUERY_CHARS = 16384
# Default top-K for retrieval per (section, category) pair.
_DEFAULT_TOP_K = 8


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


def _build_credential():
    """Pick the credential for downstream Azure calls.

    Priority:
    1. **OBO** — when an authenticated user assertion is in scope (set by the
       Flask layer) and Entra OBO is configured, run downstream calls in the
       signed-in user's context.
    2. **Azure CLI** locally (developer login).
    3. **DefaultAzureCredential** in Azure-hosted runtimes (managed identity).
    """
    from . import auth

    obo = auth.current_credential()
    if obo is not None:
        return obo
    if not _running_in_azure_host():
        tenant_id = os.getenv("AZURE_TENANT_ID") or None
        return AzureCliCredential(tenant_id=tenant_id)
    return DefaultAzureCredential()


def _retrieve_for_section(
    section_text: str, category: str, top_k: int = _DEFAULT_TOP_K
) -> list[dict[str, Any]]:
    """Run hybrid+semantic search for a section, filtered by category.

    Returns the raw search hit dicts produced by ``search.query.search_policies``.
    Raises whatever ``search_policies`` raises — callers handle the error path.
    """
    from search.query import search_policies

    query = section_text[:_SEARCH_QUERY_CHARS] if section_text else ""
    return search_policies(query=query, category=category, top=top_k)


def _format_retrieved_policies(hits: list[dict[str, Any]]) -> str:
    """Render the [Retrieved Policies] prompt block from search hits.

    Header fallback: with the DocumentIntelligenceLayoutSkill pipeline the
    per-chunk ``header`` field is null (the skill in outputFormat=text mode
    does not surface section headings as a separate field). When that
    happens we use the chunk's ``category`` as the principle label, which
    is a real semantic value populated by the AOAI categorize skill at
    ingest time (e.g. ``Reliability``, ``Network``). The agent then cites
    the category as the principle, which is more meaningful than the
    placeholder ``(no header)`` the old fallback emitted.
    """
    if not hits:
        return "(none)"
    lines: list[str] = []
    for i, h in enumerate(hits, 1):
        cat = h.get("category") or ""
        header = h.get("header") or cat or "(no header)"
        score = h.get("@rerank") or h.get("@score") or 0.0
        content = (h.get("content") or "")[:_POLICY_SNIPPET_CHARS]
        lines.append(
            f"--- Policy {i} ---\n"
            f"Header: {header}\n"
            f"Category: {cat}\n"
            f"Score: {score:.4f}\n"
            f"Content:\n{content}\n"
        )
    return "\n".join(lines)


def _build_search_failed_finding(
    section: str, category: str, error: Exception
) -> dict[str, Any]:
    """Synthesise a deterministic finding for a failed retrieval call."""
    return {
        "Type": "Error",
        "Issue": "search_failed",
        "Description": f"Retrieval for section '{section}' (category '{category}') "
                       f"failed: {error}",
        "Principles": "",
        "Mandatory": False,
        "Category": category,
    }


async def _call_agent(client: Any, agent_name: str, prompt: str, config: Config) -> str:
    """Invoke a hosted agent and return its assistant text."""
    try:
        # OpenAI sync client — run blocking call in a thread to keep asyncio
        # fan-out non-blocking.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: _sync_invoke(client, agent_name, prompt, config)
        )
    except AgentNotFoundError:
        raise
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "404" in msg or "not found" in msg.lower():
            raise AgentNotFoundError(agent_name) from e
        raise AgentInvocationError(agent_name, msg) from e


def _get_agent_openai_client(project_client: Any, config: Config, agent_name: str) -> Any:
    """Return an OpenAI client scoped to the named Foundry v2 hosted agent.

    Uses ``AIProjectClient.get_openai_client(agent_name=...)`` which sets the
    base URL to the agent's ``/endpoint/protocols/openai/`` path. Cached per
    (project endpoint, agent name).
    """
    cache_key = (config.foundry_project_endpoint, agent_name)
    cached = _OAI_CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    with _OAI_CLIENT_LOCK:
        cached = _OAI_CLIENT_CACHE.get(cache_key)
        if cached is not None:
            return cached
        oai = project_client.get_openai_client(agent_name=agent_name)
        _OAI_CLIENT_CACHE[cache_key] = oai
        return oai


def _sync_invoke(client: Any, agent_name: str, prompt: str, config: Config) -> str:
    """Invoke a Foundry v2 hosted agent via the Responses API.

    The ``model`` parameter MUST be the agent's underlying model deployment
    name (Foundry rejects mismatches with HTTP 400 invalid_payload).
    """
    oai = _get_agent_openai_client(client, config, agent_name)
    response = oai.responses.create(
        model=config.foundry_model_deployment,
        input=prompt,
    )
    text = getattr(response, "output_text", None)
    if text:
        return text
    # Fallback: walk the structured output if output_text helper is unset.
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for c in getattr(item, "content", []) or []:
            t = getattr(c, "text", None)
            if t is None:
                continue
            parts.append(getattr(t, "value", str(t)))
    return "\n".join(parts)


def _parse_findings(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[:-3]
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [{
            "Type": "Error",
            "Issue": "agent_output_unparseable",
            "Description": raw[:500],
            "Principles": "",
            "Mandatory": False,
            "Category": "",
        }]
    if isinstance(data, dict):
        data = [data]
    return data if isinstance(data, list) else []


def build_project_client(config: Config) -> Any:
    """Construct an AIProjectClient (Foundry v2 runtime) with DefaultAzureCredential.

    ``allow_preview=True`` is required for ``get_openai_client(agent_name=...)``,
    which scopes the OpenAI client to the agent's Responses API endpoint.
    Foundry v2 hosted prompt agents are invoked via this path; the legacy
    ``azure-ai-agents`` SDK's threads/messages/runs surface is for ``asst_xxx``
    classic Assistants only and does not work here.
    """
    config.require_runtime()
    try:
        from azure.ai.projects import AIProjectClient
    except ImportError as e:  # pragma: no cover
        raise AgentInvocationError(
            "ValidateArbAgent",
            "azure-ai-projects not installed. `pip install -r requirements.txt`",
        ) from e
    return AIProjectClient(
        endpoint=config.foundry_project_endpoint,
        credential=_build_credential(),
        allow_preview=True,
    )


async def validate_arb_sections(
    arb: dict[str, Any],
    config: Config | None = None,
    client: Any | None = None,
) -> list[dict]:
    """Validate each ASD section against the policy index. Returns combined findings."""
    cfg = config or Config()
    cli = client or build_project_client(cfg)
    tasks: list[asyncio.Task] = []
    findings_from_search_failures: list[dict[str, Any]] = []

    def _stringify(content: Any) -> str:
        if isinstance(content, list):
            return "\n".join(json.dumps(item, ensure_ascii=False) for item in content)
        return str(content) if content is not None else ""

    for section, content in arb.items():
        text = _stringify(content)
        if not text or "N/A" in text:
            continue
        # Use the canonical PolicyCategory enum values (string form) — preserves
        # the existing on-the-wire contract with search/query.py (which compares
        # against the index's ``category`` string field).
        category_enums = ASD_SECTION_CATEGORIES.get(section, DEFAULT_SECTION_CATEGORIES)
        categories = [c.value for c in category_enums]
        for category in categories:
            # Orchestrator-driven retrieval: pull policies for this (section,
            # category) BEFORE invoking the agent. The agent never calls
            # search itself — it reasons over the [Retrieved Policies] block.
            try:
                hits = _retrieve_for_section(text, category)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "search failed for section=%s category=%s: %s",
                    section, category, e,
                )
                findings_from_search_failures.append(
                    _build_search_failed_finding(section, category, e)
                )
                continue

            policies_block = _format_retrieved_policies(hits)
            prompt = (
                f"{SYSTEM_PROMPT}\n\n"
                f"[Section Name]\n{section}\n\n"
                f"[Policy Category Filter]\n{category}\n\n"
                f"[Retrieved Policies]\n{policies_block}\n\n"
                f"[Section Content]\n{text}\n"
            )
            tasks.append(asyncio.create_task(
                _call_agent(cli, cfg.validate_agent_name, prompt, cfg)
            ))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    findings: list[dict] = list(findings_from_search_failures)
    for r in results:
        if isinstance(r, Exception):
            logger.warning("agent call failed: %s", r)
            findings.append({
                "Type": "Error",
                "Issue": "agent_call_failed",
                "Description": str(r),
                "Principles": "",
                "Mandatory": False,
                "Category": "",
            })
            continue
        findings.extend(_parse_findings(r))
    return findings


# ---------------------------------------------------------------------------
# Chunk-based validation path (issue #65)
# ---------------------------------------------------------------------------
#
# validate_arb_chunks is the modern entry point. Differences from
# validate_arb_sections:
#
#   * Input is the raw uploaded file bytes (PDF / DOCX), not a parsed dict.
#   * Chunking is semantic (Document Intelligence Layout) — works on any
#     customer doc, not just our reference ASD section names.
#   * Each chunk's category is decided by AOAI at validate time (same
#     CATEGORIZE_SYSTEM_PROMPT as the policy ingest skillset), so the search
#     filter is per-chunk and self-consistent with the index labels.
#   * Retrieval is hybrid (BM25 + vector) + semantic-ranker + category-filter
#     — the chunk's embedding is passed as ``vector`` to search_policies.
#
# Fan-out is per chunk (was: per section × category pair). Findings are
# aggregated identically.


async def validate_arb_chunks(
    file_bytes: bytes,
    filename: str | None = None,
    config: Config | None = None,
    client: Any | None = None,
) -> list[dict]:
    """Semantic-chunked validate of an uploaded ASD/ARB document.

    See module-level note above for the design. Returns the same shape of
    findings list as :func:`validate_arb_sections` so callers (and the
    front-end) are agnostic to which path produced them.
    """
    from .asd_chunker import chunk_asd_document
    from .categorize_chunk import categorize_chunk
    from .embeddings import embed_text

    cfg = config or Config()
    cli = client or build_project_client(cfg)

    # Step 1 — semantic chunking. A DocIntel failure becomes a single
    # deterministic Error finding so the user sees the cause instead of an
    # empty results table.
    try:
        chunks = await asyncio.get_running_loop().run_in_executor(
            None, lambda: chunk_asd_document(file_bytes, filename)
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("ASD chunking failed: %s", e)
        return [{
            "Type": "Error",
            "Issue": "chunking_failed",
            "Description": f"Could not crack ASD document via Document "
                           f"Intelligence: {e}",
            "Principles": "",
            "Mandatory": False,
            "Category": "",
        }]
    if not chunks:
        logger.info("ASD chunker returned 0 chunks; nothing to validate.")
        return []
    logger.info("ASD chunker produced %d chunks", len(chunks))

    # Step 2 — per-chunk retrieve + agent call. Each step that involves an
    # Azure call runs in the executor so the asyncio fan-out is not blocked.
    findings_from_failures: list[dict[str, Any]] = []
    tasks: list[asyncio.Task] = []

    for idx, chunk in enumerate(chunks):
        tasks.append(asyncio.create_task(
            _validate_single_chunk(cli, cfg, idx, chunk, findings_from_failures)
        ))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    findings: list[dict] = list(findings_from_failures)
    for r in results:
        if isinstance(r, Exception):
            logger.warning("chunk validate task failed: %s", r)
            findings.append({
                "Type": "Error",
                "Issue": "agent_call_failed",
                "Description": str(r),
                "Principles": "",
                "Mandatory": False,
                "Category": "",
            })
            continue
        if r:
            findings.extend(r)

    deduped = dedupe_missing_findings(findings)
    if cfg.missing_verify_enabled:
        from .verify_missing import verify_missing_findings
        doc_text = "\n\n".join(chunks)
        try:
            return await verify_missing_findings(deduped, doc_text, cfg)
        except Exception as e:  # noqa: BLE001
            logger.warning("verify_missing_findings failed; returning deduped findings: %s", e)
            return deduped
    return deduped


def _missing_key(f: dict[str, Any]) -> str | None:
    """Dedupe key for a ``Missing``-type finding, or ``None`` if not mergeable.

    Empty/whitespace ``Principles`` returns ``None`` so genuinely
    un-attributed gaps remain as separate findings instead of being merged
    into a single ambiguous bucket. See ``prompt-contracts/MISSING-DEDUP.md``
    edge case #2.
    """
    principles = (f.get("Principles") or "").strip().lower()
    return principles or None


def dedupe_missing_findings(findings: list[dict]) -> list[dict]:
    """Collapse repeated ``Missing`` findings that share the same Principle.

    Per ``prompt-contracts/MISSING-DEDUP.md``:

    * Only ``Type == "Missing"`` findings are deduped — other types pass
      through unchanged because they describe chunk-specific content.
    * Dedupe key is the normalized ``Principles`` value (strip + lowercase).
      Empty/missing ``Principles`` is treated as a unique key (never merged).
    * The first occurrence wins. Conflicts on ``Mandatory``/``Category``
      are resolved in favor of the first finding (logged at DEBUG).
    * When duplicates are collapsed, the survivor's ``Description`` is
      augmented with ``" (also missing in N other chunk[s])"``.

    Does not mutate the input list.
    """
    if not findings:
        return []

    output: list[dict] = []
    index_by_key: dict[str, int] = {}
    dup_counts: dict[int, int] = {}

    for f in findings:
        if not isinstance(f, dict) or f.get("Type") != "Missing":
            output.append(f)
            continue

        key = _missing_key(f)
        if key is None:
            output.append(dict(f))
            continue

        if key not in index_by_key:
            output.append(dict(f))
            index_by_key[key] = len(output) - 1
            continue

        survivor_idx = index_by_key[key]
        survivor = output[survivor_idx]
        dup_counts[survivor_idx] = dup_counts.get(survivor_idx, 0) + 1
        if survivor.get("Mandatory") != f.get("Mandatory"):
            logger.debug(
                "dedupe_missing: Mandatory mismatch for principle=%s (kept %s, dropped %s)",
                key, survivor.get("Mandatory"), f.get("Mandatory"),
            )
        if survivor.get("Category") != f.get("Category"):
            logger.debug(
                "dedupe_missing: Category mismatch for principle=%s (kept %s, dropped %s)",
                key, survivor.get("Category"), f.get("Category"),
            )

    for idx, n in dup_counts.items():
        survivor = output[idx]
        original_desc = str(survivor.get("Description") or "")
        suffix = f" (also missing in {n} other chunk{'s' if n != 1 else ''})"
        survivor["Description"] = original_desc + suffix

    return output


async def _validate_single_chunk(
    cli: Any,
    cfg: Config,
    chunk_idx: int,
    chunk_text: str,
    failures_sink: list[dict[str, Any]],
) -> list[dict]:
    """Categorize → embed → retrieve → call agent for one chunk."""
    from .categorize_chunk import categorize_chunk
    from .embeddings import embed_text

    loop = asyncio.get_running_loop()

    # 2a — categorize (AOAI) and embed (AOAI) in parallel; both are I/O bound.
    try:
        category_enum, vector = await asyncio.gather(
            loop.run_in_executor(None, categorize_chunk, chunk_text),
            loop.run_in_executor(None, embed_text, chunk_text),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("categorize/embed failed for chunk %d: %s", chunk_idx, e)
        failures_sink.append({
            "Type": "Error",
            "Issue": "chunk_preprocess_failed",
            "Description": f"Chunk #{chunk_idx}: {e}",
            "Principles": "",
            "Mandatory": False,
            "Category": "",
        })
        return []
    category = category_enum.value
    logger.debug("chunk %d -> category=%s (vector dim=%d)",
                 chunk_idx, category, len(vector))

    # 2b — filtered hybrid + semantic-ranked retrieval.
    try:
        hits = await loop.run_in_executor(
            None,
            lambda: _retrieve_for_chunk(chunk_text, category, vector),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("search failed for chunk %d category=%s: %s",
                       chunk_idx, category, e)
        failures_sink.append(
            _build_search_failed_finding(f"chunk-{chunk_idx}", category, e)
        )
        return []

    # 2c — render prompt, call the validate agent.
    policies_block = _format_retrieved_policies(hits)
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"[Chunk Index]\n{chunk_idx}\n\n"
        f"[Policy Category Filter]\n{category}\n\n"
        f"[Retrieved Policies]\n{policies_block}\n\n"
        f"[Section Content]\n{chunk_text}\n"
    )
    raw = await _call_agent(cli, cfg.validate_agent_name, prompt, cfg)
    return _parse_findings(raw)


def _retrieve_for_chunk(
    chunk_text: str, category: str, vector: list[float],
    top_k: int = _DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """Filtered hybrid + semantic-ranked retrieval for a single chunk.

    Passes the chunk's embedding as ``vector`` so ``search_policies`` issues
    a true hybrid query (BM25 + ANN) under the semantic ranker, with the
    AOAI-assigned category as a hard filter on the search index.

    Two robustness behaviours:

    1. ``category == "general"`` is treated as **no filter** — by definition
       a chunk in the "general" bucket could apply to any category, so we
       let the semantic ranker pick the best matches across the whole
       index rather than filter to a bucket the policy ingest skillset
       almost never assigns.
    2. If a filtered search returns zero hits we retry WITHOUT the filter
       so the agent at least sees SOMETHING relevant — better than empty
       findings driven by a mis-categorisation upstream.
    """
    from search.query import search_policies

    query = chunk_text[:_SEARCH_QUERY_CHARS] if chunk_text else ""

    # (1) general → no filter
    effective_category: str | None = category
    if (category or "").strip().lower() == "general":
        logger.debug("category=general, dropping filter for hybrid+semantic search")
        effective_category = None

    hits = search_policies(
        query=query,
        category=effective_category,
        top=top_k,
        vector=vector,
    )

    # (2) fallback to unfiltered if the filter starved us
    if not hits and effective_category is not None:
        logger.info(
            "category=%s returned 0 hits; retrying without filter so the "
            "agent has policies to reason over.",
            effective_category,
        )
        hits = search_policies(
            query=query,
            category=None,
            top=top_k,
            vector=vector,
        )
    return hits
