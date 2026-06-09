"""IaC-generator hosted agent client."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from typing import Any

from .categories import IAC_SECTIONS
from .config import Config
from .errors import AgentInvocationError
from .validate_agent import _call_agent, build_project_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an Infrastructure-as-Code assistant. Given the listed architecture "
    "design document sections, produce starter Terraform AWS scripts. Each component "
    "should begin with a `# Component:` comment that names the component. "
    "Return ONLY a JSON array of strings, where each string is a complete script. "
    "Do not include any prose outside the JSON array."
)


def _serialize(arb: dict[str, Any]) -> str:
    out: list[str] = []
    for section in IAC_SECTIONS:
        content = arb.get(section)
        if not content:
            continue
        if isinstance(content, list):
            out.append(f"## {section}\n" + json.dumps(content, ensure_ascii=False))
        else:
            text = str(content)
            if "N/A" in text:
                continue
            out.append(f"## {section}\n{text}")
    return "\n\n".join(out)


def _parse_scripts(raw: str) -> list[str]:
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
        try:
            data = ast.literal_eval(raw)
        except Exception:  # noqa: BLE001
            return [raw]
    if isinstance(data, str):
        return [data]
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


async def generate_iac(
    arb: dict[str, Any],
    config: Config | None = None,
    client: Any | None = None,
) -> list[str]:
    cfg = config or Config()
    cli = client or build_project_client(cfg)
    content = _serialize(arb)
    if not content.strip():
        return []
    prompt = f"{SYSTEM_PROMPT}\n\n[Content]\n{content}\n"
    try:
        raw = await _call_agent(cli, cfg.iac_agent_name, prompt, cfg)
    except AgentInvocationError:
        raise
    return _parse_scripts(raw)
