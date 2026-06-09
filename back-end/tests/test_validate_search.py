"""Tests for orchestrator-driven retrieval in agents/validate_agent.py (issue #53).

These tests monkeypatch ``search.query.search_policies`` and the agent runtime
client so they exercise the new ``_retrieve_for_section`` → prompt-assembly path
without any Azure calls.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agents import validate_agent as va
from agents.config import Config


class _FakeMessage:
    def __init__(self, role: str, text: str):
        self.role = role

        class _T:
            def __init__(self, v: str):
                self.value = v

        class _P:
            def __init__(self, v: str):
                self.text = _T(v)

        self.content = [_P(text)]


class _FakeMessagesOp:
    def __init__(self, capture: dict):
        self._capture = capture
        self._reply = capture.get("reply", "[]")

    def create(self, *, thread_id: str, role: str, content: str) -> None:
        self._capture["last_user_prompt"] = content

    def list(self, *, thread_id: str):
        return [_FakeMessage("assistant", self._reply)]


class _FakeThreadsOp:
    def create(self):
        class _T:
            id = "thread-1"
        return _T()


class _FakeRunsOp:
    def create_and_process(self, *, thread_id: str, agent_id: str):
        class _R:
            status = "completed"
        return _R()


class _FakeAgentsClient:
    """Minimal stand-in for azure.ai.agents.AgentsClient."""

    def __init__(self, reply: str = "[]"):
        self._capture: dict = {"reply": reply}
        self.threads = _FakeThreadsOp()
        self.messages = _FakeMessagesOp(self._capture)
        self.runs = _FakeRunsOp()

    @property
    def last_user_prompt(self) -> str:
        return self._capture.get("last_user_prompt", "")


def _patch_agent_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(va, "_resolve_agent_id", lambda name, cfg: "agent-id-1")


def _make_cfg() -> Config:
    cfg = Config()
    cfg.foundry_project_endpoint = "https://test"
    cfg.foundry_model_deployment = "gpt-test"
    return cfg


def test_prompt_contains_retrieved_policy_headers(monkeypatch: pytest.MonkeyPatch):
    _patch_agent_id(monkeypatch)
    hits = [
        {"header": "Network Segmentation", "category": "Network",
         "content": "Use NSGs.", "@rerank": 3.21},
        {"header": "Private Endpoints", "category": "Network",
         "content": "Prefer private endpoints.", "@rerank": 2.10},
    ]
    monkeypatch.setattr(va, "_retrieve_for_section",
                        lambda text, category, top_k=8: hits)

    fake = _FakeAgentsClient(reply="[]")
    arb = {"Network Requirements": "All workloads must run in a hub-spoke VNet."}
    out = asyncio.run(va.validate_arb_sections(arb, _make_cfg(), fake))

    assert out == []
    prompt = fake.last_user_prompt
    assert "[Retrieved Policies]" in prompt
    assert "Network Segmentation" in prompt
    assert "Private Endpoints" in prompt
    assert "Score: 3.2100" in prompt


def test_empty_retrieval_renders_none_block(monkeypatch: pytest.MonkeyPatch):
    _patch_agent_id(monkeypatch)
    monkeypatch.setattr(va, "_retrieve_for_section",
                        lambda text, category, top_k=8: [])
    fake = _FakeAgentsClient(reply="[]")
    arb = {"Network Requirements": "Routing through corporate firewall."}
    out = asyncio.run(va.validate_arb_sections(arb, _make_cfg(), fake))

    assert out == []
    assert "[Retrieved Policies]\n(none)" in fake.last_user_prompt


def test_search_failure_records_error_finding_and_skips_agent(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_agent_id(monkeypatch)

    def _boom(text, category, top_k=8):
        raise RuntimeError("AI Search 503")

    monkeypatch.setattr(va, "_retrieve_for_section", _boom)
    fake = _FakeAgentsClient(reply="[]")
    arb = {"Network Requirements": "All workloads must run in a hub-spoke VNet."}
    out = asyncio.run(va.validate_arb_sections(arb, _make_cfg(), fake))

    # The orchestrator records a deterministic finding for the failed retrieval
    # and does NOT call the agent for that (section, category) pair.
    assert len(out) == 1
    assert out[0]["Type"] == "Error"
    assert out[0]["Issue"] == "search_failed"
    assert "AI Search 503" in out[0]["Description"]
    assert fake.last_user_prompt == ""  # agent was not invoked


def test_agent_findings_are_merged_with_search_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_agent_id(monkeypatch)

    # First call succeeds (returns one finding), second call's retrieval fails.
    calls: dict[str, int] = {"n": 0}

    def _retrieve(text, category, top_k=8):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("transient")
        return [{"header": "Policy A", "category": category,
                 "content": "", "@rerank": 1.0}]

    monkeypatch.setattr(va, "_retrieve_for_section", _retrieve)
    agent_finding = json.dumps([{
        "Type": "Violation", "Issue": "x", "Description": "y",
        "Principles": "Policy A", "Mandatory": True, "Category": "Storage and Data",
    }])
    fake = _FakeAgentsClient(reply=agent_finding)

    # Storage Requirements has TWO categories → fans out two retrievals.
    arb = {"Storage Requirements": "Use S3 for blob storage."}
    out = asyncio.run(va.validate_arb_sections(arb, _make_cfg(), fake))

    issues = sorted(f["Issue"] for f in out)
    assert issues == ["search_failed", "x"]
