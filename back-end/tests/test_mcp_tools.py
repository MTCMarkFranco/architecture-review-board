"""Unit tests for mcp_server.tools — confirms tools delegate to the orchestrator."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from mcp_server.auth import AuthContext, set_current_auth_for_tests
from mcp_server.config import McpConfig
from mcp_server.tools import register_tools


class _FakeWorkflow:
    def __init__(self):
        self.calls: list[tuple[str, bytes, str]] = []

    async def validate_bytes(self, file_bytes: bytes, filename: str) -> list[dict[str, Any]]:
        self.calls.append(("validate_bytes", file_bytes, filename))
        return [{"Type": "Missing", "Issue": "x", "Description": "y",
                 "Principles": "p", "Mandatory": True, "Category": "c"}]

    async def iac_bytes(self, file_bytes: bytes, filename: str) -> list[str]:
        self.calls.append(("iac_bytes", file_bytes, filename))
        return ["resource \"x\" \"y\" {}"]


@pytest.fixture()
def mcp_fixture() -> tuple[FastMCP, _FakeWorkflow]:
    mcp = FastMCP(name="test-arb-bot-mcp")
    wf = _FakeWorkflow()
    register_tools(mcp, mcp_cfg=McpConfig(), workflow=wf)  # type: ignore[arg-type]
    return mcp, wf


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def _call(mcp: FastMCP, tool_name: str, args: dict[str, Any]) -> list[Any]:
    """Call an MCP-registered tool by name and return its structured content."""
    result = await mcp.call_tool(tool_name, args)
    # mcp >=1.2 returns (list[Content], dict|None) — we just want the structured payload.
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], (dict, list, type(None))):
        return result[1] if result[1] is not None else result[0]
    return result


def test_validate_arb_with_inline_bytes(mcp_fixture):
    mcp, wf = mcp_fixture
    payload = _b64(b"%PDF-1.4 hi")
    out = asyncio.run(_call(mcp, "validate_arb", {
        "file_bytes_b64": payload, "filename": "sample.pdf",
    }))
    assert wf.calls[-1][0] == "validate_bytes"
    assert wf.calls[-1][2] == "sample.pdf"
    # Output is the findings list returned by the fake workflow.
    findings = out if isinstance(out, list) else out.get("result") or out
    assert any(f.get("Type") == "Missing" for f in findings)


def test_generate_iac_with_inline_bytes(mcp_fixture):
    mcp, wf = mcp_fixture
    payload = _b64(b"PK fake docx")
    out = asyncio.run(_call(mcp, "generate_iac", {
        "file_bytes_b64": payload, "filename": "sample.docx",
    }))
    assert wf.calls[-1][0] == "iac_bytes"
    scripts = out if isinstance(out, list) else out.get("result") or out
    assert any("resource" in s for s in scripts)


def test_validate_arb_rejects_unsupported_extension(mcp_fixture):
    mcp, _wf = mcp_fixture
    payload = _b64(b"junk")
    with pytest.raises(Exception) as ei:
        asyncio.run(_call(mcp, "validate_arb", {
            "file_bytes_b64": payload, "filename": "malware.exe",
        }))
    assert "unsupported_file_type" in str(ei.value) or "Unsupported" in str(ei.value)


def test_validate_arb_rejects_both_inline_and_reference(mcp_fixture):
    mcp, _wf = mcp_fixture
    payload = _b64(b"%PDF-1.4 hi")
    with pytest.raises(Exception) as ei:
        asyncio.run(_call(mcp, "validate_arb", {
            "file_bytes_b64": payload,
            "filename": "sample.pdf",
            "file_reference": {"name": "other.pdf", "driveId": "D", "itemId": "I"},
        }))
    assert "either" in str(ei.value).lower()


def test_validate_arb_via_sharepoint_reference(monkeypatch, mcp_fixture):
    mcp, wf = mcp_fixture

    # Stub OBO + download so no Graph call happens.
    monkeypatch.setattr(
        "mcp_server.tools.acquire_graph_token_obo",
        lambda assertion, cfg: "graph-tok",
    )
    monkeypatch.setattr(
        "mcp_server.tools.download_file",
        lambda ref, tok, cfg, http_client=None: (b"%PDF-1.4 fake", "from-sp.pdf", "application/pdf"),
    )

    # Bind a fake auth context.
    token = set_current_auth_for_tests(AuthContext(
        user_assertion="caller-jwt",
        claims={"oid": "user-1", "preferred_username": "x@y.com"},
    ))
    try:
        out = asyncio.run(_call(mcp, "validate_arb", {
            "file_reference": {"name": "from-sp.pdf", "driveId": "D", "itemId": "I"},
        }))
    finally:
        set_current_auth_for_tests(None)

    assert wf.calls[-1][0] == "validate_bytes"
    assert wf.calls[-1][2] == "from-sp.pdf"
    findings = out if isinstance(out, list) else out
    assert findings  # non-empty


def test_list_policy_categories(mcp_fixture):
    mcp, _wf = mcp_fixture
    out = asyncio.run(_call(mcp, "list_policy_categories", {}))
    cats = out["result"] if isinstance(out, dict) and "result" in out else out
    assert "Reliability" in cats
    assert "general" in cats
