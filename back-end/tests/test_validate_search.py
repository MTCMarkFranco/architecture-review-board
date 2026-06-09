"""Tests for orchestrator-driven retrieval in agents/validate_agent.py (issue #53).

These tests monkeypatch ``search.query.search_policies`` and the agent runtime
client so they exercise the new ``_retrieve_for_section`` → prompt-assembly path
without any Azure calls.

Runtime path is the Foundry v2 Responses API (issue #55):
``project.get_openai_client(agent_name=...).responses.create(model=..., input=...)``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agents import validate_agent as va
from agents.config import Config


class _FakeResponse:
    def __init__(self, text: str):
        self.output_text = text


class _FakeResponsesOp:
    def __init__(self, capture: dict):
        self._capture = capture
        self._reply = capture.get("reply", "[]")

    def create(self, *, model: str, input: str, **kwargs: Any) -> _FakeResponse:
        self._capture["last_user_prompt"] = input
        self._capture["last_model"] = model
        return _FakeResponse(self._reply)


class _FakeOpenAIClient:
    def __init__(self, capture: dict):
        self.responses = _FakeResponsesOp(capture)


class _FakeProjectClient:
    """Minimal stand-in for azure.ai.projects.AIProjectClient."""

    def __init__(self, reply: str = "[]"):
        self._capture: dict = {"reply": reply}
        self._oai = _FakeOpenAIClient(self._capture)

    def get_openai_client(self, *, agent_name: str) -> _FakeOpenAIClient:
        self._capture["last_agent_name"] = agent_name
        return self._oai

    @property
    def last_user_prompt(self) -> str:
        return self._capture.get("last_user_prompt", "")

    @property
    def last_model(self) -> str:
        return self._capture.get("last_model", "")


def _make_cfg() -> Config:
    cfg = Config()
    cfg.foundry_project_endpoint = "https://test"
    cfg.foundry_model_deployment = "gpt-test"
    return cfg


@pytest.fixture(autouse=True)
def _reset_oai_cache():
    # Each test starts with an empty OpenAI client cache so the fake client
    # supplied via the ``client=`` arg is actually consulted.
    va._OAI_CLIENT_CACHE.clear()
    yield
    va._OAI_CLIENT_CACHE.clear()


def test_prompt_contains_retrieved_policy_headers(monkeypatch: pytest.MonkeyPatch):
    hits = [
        {"header": "Network Segmentation", "category": "Network",
         "content": "Use NSGs.", "@rerank": 3.21},
        {"header": "Private Endpoints", "category": "Network",
         "content": "Prefer private endpoints.", "@rerank": 2.10},
    ]
    monkeypatch.setattr(va, "_retrieve_for_section",
                        lambda text, category, top_k=8: hits)

    fake = _FakeProjectClient(reply="[]")
    arb = {"Network Requirements": "All workloads must run in a hub-spoke VNet."}
    out = asyncio.run(va.validate_arb_sections(arb, _make_cfg(), fake))

    assert out == []
    prompt = fake.last_user_prompt
    assert "[Retrieved Policies]" in prompt
    assert "Network Segmentation" in prompt
    assert "Private Endpoints" in prompt
    assert "Score: 3.2100" in prompt
    assert fake.last_model == "gpt-test"


def test_empty_retrieval_renders_none_block(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(va, "_retrieve_for_section",
                        lambda text, category, top_k=8: [])
    fake = _FakeProjectClient(reply="[]")
    arb = {"Network Requirements": "Routing through corporate firewall."}
    out = asyncio.run(va.validate_arb_sections(arb, _make_cfg(), fake))

    assert out == []
    assert "[Retrieved Policies]\n(none)" in fake.last_user_prompt


def test_search_failure_records_error_finding_and_skips_agent(
    monkeypatch: pytest.MonkeyPatch,
):
    def _boom(text, category, top_k=8):
        raise RuntimeError("AI Search 503")

    monkeypatch.setattr(va, "_retrieve_for_section", _boom)
    fake = _FakeProjectClient(reply="[]")
    arb = {"Network Requirements": "All workloads must run in a hub-spoke VNet."}
    out = asyncio.run(va.validate_arb_sections(arb, _make_cfg(), fake))

    assert len(out) == 1
    assert out[0]["Type"] == "Error"
    assert out[0]["Issue"] == "search_failed"
    assert "AI Search 503" in out[0]["Description"]
    assert fake.last_user_prompt == ""  # agent was not invoked


def test_agent_findings_are_merged_with_search_errors(
    monkeypatch: pytest.MonkeyPatch,
):
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
    fake = _FakeProjectClient(reply=agent_finding)

    # Storage Requirements has TWO categories → fans out two retrievals.
    arb = {"Storage Requirements": "Use S3 for blob storage."}
    out = asyncio.run(va.validate_arb_sections(arb, _make_cfg(), fake))

    issues = sorted(f["Issue"] for f in out)
    assert issues == ["search_failed", "x"]
