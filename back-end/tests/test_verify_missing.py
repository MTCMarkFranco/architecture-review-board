"""Tests for ``verify_missing_findings`` (issue #77).

See ``prompt-contracts/MISSING-VERIFY.md`` for the behavior spec; each test
maps to one of the 13 edge cases enumerated there.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agents import verify_missing as vm
from agents import validate_agent as va
from agents.config import Config


def _missing(principles: str, *, description: str = "Policy X is required.",
             category: str = "resilience", mandatory: bool = True) -> dict:
    return {
        "Type": "Missing",
        "Issue": "x",
        "Description": description,
        "Principles": principles,
        "Mandatory": mandatory,
        "Category": category,
    }


class _FakeChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, responder):
        self._responder = responder
        self.calls: list[dict] = []

    def create(self, *, model, messages, **kwargs):
        self.calls.append({"model": model, "messages": messages, **kwargs})
        content = self._responder(messages)
        if isinstance(content, BaseException):
            raise content
        return _FakeResponse(content)


class _FakeChat:
    def __init__(self, responder):
        self.completions = _FakeCompletions(responder)


class _FakeClient:
    def __init__(self, responder):
        self.chat = _FakeChat(responder)


def _make_cfg(*, enabled: bool = True, cap: int = 10) -> Config:
    cfg = Config()
    cfg.missing_verify_enabled = enabled
    cfg.missing_verify_max = cap
    return cfg


def _run(coro):
    return asyncio.run(coro)


def _set_env(monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENDPOINT", "https://fake.cognitiveservices.azure.com/")
    monkeypatch.setenv("FOUNDRY_CATEGORIZE_DEPLOYMENT", "gpt-fake")


# ---- helper-level tests --------------------------------------------------

def test_strip_chunk_count_suffix_singular():
    s = "Policy X is required. (also missing in 1 other chunk)"
    assert vm._strip_chunk_count_suffix(s) == "Policy X is required."


def test_strip_chunk_count_suffix_plural():
    s = "Policy X is required. (also missing in 26 other chunks)"
    assert vm._strip_chunk_count_suffix(s) == "Policy X is required."


def test_strip_chunk_count_suffix_noop():
    s = "Policy X is required."
    assert vm._strip_chunk_count_suffix(s) == s


def test_strip_chunk_count_suffix_idempotent():
    s = "Policy X is required. (also missing in 5 other chunks)"
    once = vm._strip_chunk_count_suffix(s)
    twice = vm._strip_chunk_count_suffix(once)
    assert once == twice


def test_parse_verify_response_true_with_quote():
    raw = '{"present": true, "quote": "RTO is 4h"}'
    assert vm._parse_verify_response(raw) == (True, "RTO is 4h")


def test_parse_verify_response_false():
    raw = '{"present": false, "quote": ""}'
    assert vm._parse_verify_response(raw) == (False, "")


def test_parse_verify_response_code_fence():
    raw = '```json\n{"present": true, "quote": "x"}\n```'
    assert vm._parse_verify_response(raw) == (True, "x")


def test_parse_verify_response_surrounded_text():
    raw = 'Sure, here is the answer: {"present": false, "quote": ""} ok?'
    assert vm._parse_verify_response(raw) == (False, "")


def test_parse_verify_response_malformed():
    assert vm._parse_verify_response("definitely missing") == (None, None)
    assert vm._parse_verify_response("") == (None, None)
    assert vm._parse_verify_response('{"present": "maybe"}') == (None, None)


def test_truncate_doc_text_under_budget():
    s = "x" * 100
    assert vm._truncate_doc_text(s) == s


def test_truncate_doc_text_over_budget():
    s = "x" * (vm._DOC_TEXT_CHAR_BUDGET + 5000)
    out = vm._truncate_doc_text(s)
    assert len(out) == vm._DOC_TEXT_CHAR_BUDGET
    assert out.endswith(vm._DOC_TRUNCATION_MARKER)


# ---- verify_missing_findings tests ---------------------------------------

def test_disabled_passthrough(monkeypatch):
    cfg = _make_cfg(enabled=False)
    findings = [_missing("rto-rpo")]
    out = _run(vm.verify_missing_findings(findings, "doc text", cfg, client=object()))
    assert out == findings


def test_no_missing_passthrough(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    findings = [
        {"Type": "Violation", "Issue": "v", "Description": "vd",
         "Principles": "p", "Mandatory": True, "Category": "c"},
    ]
    fake_client = _FakeClient(lambda m: pytest.fail("must not call model"))
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    assert out == findings
    assert fake_client.chat.completions.calls == []


def test_all_present_drops_findings(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    findings = [
        _missing("rto-rpo"),
        _missing("logging"),
    ]
    fake_client = _FakeClient(lambda m: '{"present": true, "quote": "found it"}')
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    assert out == []
    assert len(fake_client.chat.completions.calls) == 2


def test_all_absent_rewrites_descriptions(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    findings = [
        _missing("rto-rpo",
                 description="RPO/RTO not declared. (also missing in 26 other chunks)"),
        _missing("logging",
                 description="Centralized logging not specified. (also missing in 9 other chunks)"),
    ]
    fake_client = _FakeClient(lambda m: '{"present": false, "quote": ""}')
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    assert len(out) == 2
    for f in out:
        assert "(also missing in" not in f["Description"]
        assert f["Description"].endswith("Not defined anywhere in the document.")


def test_mixed_present_and_absent(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    findings = [
        _missing("rto-rpo", description="RPO/RTO not declared."),
        _missing("logging", description="No central logging."),
    ]

    def responder(messages):
        # System + user; user message contains "PRINCIPLE:\n<name>\n\n..."
        user = next(m["content"] for m in messages if m["role"] == "user")
        if "rto-rpo" in user:
            return '{"present": true, "quote": "RPO 15m / RTO 1h"}'
        return '{"present": false, "quote": ""}'

    fake_client = _FakeClient(responder)
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    assert len(out) == 1
    assert out[0]["Principles"] == "logging"
    assert out[0]["Description"].endswith("Not defined anywhere in the document.")


def test_cap_exceeded_tail_unchanged(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg(cap=2)
    findings = [
        _missing("p-a", description="A. (also missing in 3 other chunks)"),
        _missing("p-b", description="B. (also missing in 2 other chunks)"),
        _missing("p-c", description="C. (also missing in 4 other chunks)"),
    ]
    fake_client = _FakeClient(lambda m: '{"present": false, "quote": ""}')
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    # Only first two verified — rewritten; third unchanged.
    assert len(out) == 3
    assert out[0]["Description"].endswith("Not defined anywhere in the document.")
    assert out[1]["Description"].endswith("Not defined anywhere in the document.")
    assert out[2]["Description"] == "C. (also missing in 4 other chunks)"


def test_model_call_failure_passthrough(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    findings = [_missing("p-a", description="A. (also missing in 5 other chunks)")]
    fake_client = _FakeClient(lambda m: RuntimeError("boom"))
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    assert out == findings  # unchanged


def test_malformed_json_passthrough(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    findings = [_missing("p-a", description="A. (also missing in 5 other chunks)")]
    fake_client = _FakeClient(lambda m: "I think it's missing, yes.")
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    assert out == findings


def test_empty_principle_passthrough(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    findings = [_missing("", description="Unattributed gap.")]
    fake_client = _FakeClient(lambda m: pytest.fail("must not call model"))
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    assert out == findings


def test_idempotent_on_already_rewritten(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    findings = [
        _missing("p-a", description="A. Not defined anywhere in the document."),
    ]
    fake_client = _FakeClient(lambda m: '{"present": false, "quote": ""}')
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    # Sentence not duplicated.
    assert out[0]["Description"].count("Not defined anywhere in the document.") == 1


def test_does_not_mutate_input(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    original = _missing("p-a", description="A.")
    findings = [original]
    fake_client = _FakeClient(lambda m: '{"present": false, "quote": ""}')
    _ = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    assert original["Description"] == "A."  # input untouched


def test_order_preserved_with_drops(monkeypatch):
    _set_env(monkeypatch)
    cfg = _make_cfg()
    findings = [
        _missing("p-a", description="A."),
        {"Type": "Violation", "Issue": "v", "Description": "vd",
         "Principles": "anything", "Mandatory": True, "Category": "c"},
        _missing("p-b", description="B."),
        _missing("p-c", description="C."),
    ]

    def responder(messages):
        user = next(m["content"] for m in messages if m["role"] == "user")
        # Drop p-b; keep p-a and p-c.
        if "p-b" in user:
            return '{"present": true, "quote": "found"}'
        return '{"present": false, "quote": ""}'

    fake_client = _FakeClient(responder)
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    types_principles = [(f["Type"], f.get("Principles")) for f in out]
    assert types_principles == [
        ("Missing", "p-a"),
        ("Violation", "anything"),
        ("Missing", "p-c"),
    ]


def test_missing_env_skips_verify(monkeypatch):
    # No FOUNDRY_ENDPOINT set → verify can't run; should passthrough.
    monkeypatch.delenv("FOUNDRY_ENDPOINT", raising=False)
    monkeypatch.delenv("FOUNDRY_CATEGORIZE_DEPLOYMENT", raising=False)
    monkeypatch.delenv("FOUNDRY_MODEL_DEPLOYMENT", raising=False)
    cfg = _make_cfg()
    findings = [_missing("p-a", description="A. (also missing in 3 other chunks)")]
    fake_client = _FakeClient(lambda m: pytest.fail("must not call model"))
    out = _run(vm.verify_missing_findings(findings, "doc", cfg, client=fake_client))
    assert out == findings


# ---- integration with validate_arb_chunks --------------------------------

def test_validate_arb_chunks_runs_verify(monkeypatch):
    """validate_arb_chunks → dedupe → verify pipeline drops false positives."""
    _set_env(monkeypatch)

    def fake_chunk(file_bytes, filename):
        return ["chunk-0 about rto", "chunk-1 about rto", "chunk-2 about rto"]

    async def fake_validate_single(cli, cfg, idx, chunk, failures):
        return [_missing("rto-rpo", description=f"from-{idx}")]

    monkeypatch.setattr(va, "_validate_single_chunk", fake_validate_single)

    import sys
    fake_chunker = type(sys)("agents.asd_chunker")
    fake_chunker.chunk_asd_document = fake_chunk  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents.asd_chunker", fake_chunker)

    # Make verify_missing think the principle IS present in the doc → drop.
    async def fake_verify(findings, doc_text, cfg, client=None):
        return [f for f in findings if f.get("Type") != "Missing"]

    monkeypatch.setattr(vm, "verify_missing_findings", fake_verify)
    # Also patch the symbol used inside validate_agent's lazy import.
    import agents.verify_missing as vm_mod
    monkeypatch.setattr(vm_mod, "verify_missing_findings", fake_verify)

    cfg = Config()
    cfg.missing_verify_enabled = True
    cfg.missing_verify_max = 10

    out = asyncio.run(
        va.validate_arb_chunks(b"bytes", "f.pdf", cfg, client=object())
    )
    assert not any(f.get("Type") == "Missing" for f in out)
