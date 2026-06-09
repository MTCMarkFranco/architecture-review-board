"""Validate-ARB hosted agent client.

Calls a Foundry v2 hosted prompt agent ``ValidateArbAgent`` per ASD section
and aggregates the JSON findings into a single list.

Uses ``azure-ai-projects`` v2 for agent definition lookup (name → id) and
``azure-ai-agents`` for runtime threads/messages/runs. Both authenticate via
``DefaultAzureCredential`` (no API keys).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from azure.identity import DefaultAzureCredential

from .config import Config
from .errors import AgentInvocationError, AgentNotFoundError

logger = logging.getLogger(__name__)

# Module-level cache of agent name → agent id. Agent ids are stable for the
# lifetime of a deployment, so caching avoids a round-trip per section call.
_AGENT_ID_CACHE: dict[str, str] = {}
_AGENT_ID_LOCK = threading.Lock()

# Section → policy categories (used to filter the AI Search index per call).
SECTION_CATEGORIES: dict[str, list[str]] = {
    "Introduction": ["Operational Excellence"],
    "Key Functionalities/Capabilities": ["Operational Excellence"],
    "Assumptions/Constraints/Recommendations": ["Reliability"],
    "User/Usage Requirements": ["Operational Excellence"],
    "Interface Requirements": ["Security and Governance"],
    "Security Requirements": ["Security and Governance"],
    "Network Requirements": ["Network"],
    "Software Requirements": ["Operational Excellence"],
    "Performance Requirements": ["Performance and Efficiency"],
    "Supportability Requirements": ["Operational Excellence"],
    "Storage Requirements": ["Storage and Data", "Cost Optimization"],
    "Database Requirements": ["Storage and Data"],
    "Disaster Recovery Requirements": ["Reliability"],
    "Compliance Requirements": ["Security and Governance"],
    "Licensing Requirements": ["Cost Optimization"],
    "Proposed Solution": ["Operational Excellence", "Reliability"],
    "EC2 Sizing/Specifications": ["Cost Optimization"],
    "On-Prem Servers Sizing/Specification": ["Cost Optimization"],
    "Deployment Details": ["Security and Governance"],
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
        # azure-ai-agents v1 surface — synchronous client, run in thread
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


def _resolve_agent_id(agent_name: str, config: Config) -> str:
    """Look up a hosted agent's id by name via AIProjectClient. Cached."""
    cached = _AGENT_ID_CACHE.get(agent_name)
    if cached:
        return cached
    with _AGENT_ID_LOCK:
        cached = _AGENT_ID_CACHE.get(agent_name)
        if cached:
            return cached
        try:
            from azure.ai.projects import AIProjectClient
        except ImportError as e:  # pragma: no cover
            raise AgentInvocationError(
                agent_name,
                "azure-ai-projects not installed. `pip install -r requirements.txt`",
            ) from e
        proj = AIProjectClient(
            endpoint=config.foundry_project_endpoint,
            credential=DefaultAzureCredential(),
        )
        try:
            details = proj.agents.get(agent_name)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "404" in msg or "not found" in msg.lower():
                raise AgentNotFoundError(agent_name) from e
            raise AgentInvocationError(agent_name, msg) from e
        agent_id = getattr(details, "id", None)
        if not agent_id:
            raise AgentInvocationError(
                agent_name, "agent definition has no id"
            )
        _AGENT_ID_CACHE[agent_name] = agent_id
        return agent_id


def _sync_invoke(client: Any, agent_name: str, prompt: str, config: Config) -> str:
    # Create a thread, post a user message, run the agent, return assistant text.
    agent_id = _resolve_agent_id(agent_name, config)
    thread = client.threads.create()
    client.messages.create(
        thread_id=thread.id, role="user", content=prompt
    )
    run = client.runs.create_and_process(
        thread_id=thread.id, agent_id=agent_id
    )
    if getattr(run, "status", "") == "failed":
        raise RuntimeError(f"run failed: {getattr(run, 'last_error', '')}")
    msgs = list(client.messages.list(thread_id=thread.id))
    for m in msgs:
        if getattr(m, "role", "") == "assistant":
            parts = getattr(m, "content", []) or []
            text_parts = []
            for p in parts:
                t = getattr(p, "text", None)
                if t is not None:
                    text_parts.append(getattr(t, "value", str(t)))
            if text_parts:
                return "\n".join(text_parts)
    return ""


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
    """Construct an AgentsClient (runtime threads/messages/runs) with DefaultAzureCredential.

    Name retained for backward compatibility with the orchestrator. The returned
    client is an ``azure.ai.agents.AgentsClient`` — the runtime surface in the
    Foundry v2 SDK split.
    """
    config.require_runtime()
    try:
        from azure.ai.agents import AgentsClient
    except ImportError as e:  # pragma: no cover
        raise AgentInvocationError(
            "ValidateArbAgent",
            "azure-ai-agents not installed. `pip install -r requirements.txt`",
        ) from e
    return AgentsClient(
        endpoint=config.foundry_project_endpoint,
        credential=DefaultAzureCredential(),
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
        categories = SECTION_CATEGORIES.get(section, ["general"])
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
