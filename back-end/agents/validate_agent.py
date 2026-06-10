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
    "Identify violations and deviations. When you reference a policy header, "
    "replace underscores with spaces.\n\n"
    "Return ONE JSON object per finding with the schema:\n"
    "{\"Type\": \"Violation|Deviation\", \"Issue\": \"<short title>\","
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


def _build_credential() -> DefaultAzureCredential:
    """Prefer Azure CLI locally; only probe managed identity in Azure hosts."""
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
    """Render the [Retrieved Policies] prompt block from search hits."""
    if not hits:
        return "(none)"
    lines: list[str] = []
    for i, h in enumerate(hits, 1):
        header = h.get("header") or "(no header)"
        cat = h.get("category") or ""
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
