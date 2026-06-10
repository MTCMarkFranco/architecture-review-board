"""Tests for ``dedupe_missing_findings`` (issue #73).

See ``prompt-contracts/MISSING-DEDUP.md`` for the behavior spec; each test
below maps to one of the edge cases enumerated there.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from agents import validate_agent as va
from agents.config import Config


def _missing(principles: str, *, issue: str = "i", description: str = "d",
             category: str = "resilience", mandatory: bool = True) -> dict:
    return {
        "Type": "Missing",
        "Issue": issue,
        "Description": description,
        "Principles": principles,
        "Mandatory": mandatory,
        "Category": category,
    }


def test_empty_input_returns_empty():
    assert va.dedupe_missing_findings([]) == []


def test_no_missing_passthrough():
    findings = [
        {"Type": "Violation", "Issue": "v", "Description": "d",
         "Principles": "p1", "Mandatory": True, "Category": "c"},
        {"Type": "Suggestion", "Issue": "s", "Description": "d",
         "Principles": "p1", "Mandatory": False, "Category": "c"},
    ]
    out = va.dedupe_missing_findings(findings)
    assert out == findings
    assert out is not findings  # new list


def test_single_missing_no_suffix():
    f = _missing("rto-rpo")
    out = va.dedupe_missing_findings([f])
    assert len(out) == 1
    assert out[0]["Description"] == "d"  # no suffix added
    assert out[0]["Principles"] == "rto-rpo"


def test_n_duplicates_collapse_with_count():
    findings = [_missing("rto-rpo", description=f"desc-{i}") for i in range(6)]
    out = va.dedupe_missing_findings(findings)
    assert len(out) == 1
    # Survivor is the first occurrence
    assert out[0]["Description"] == "desc-0 (also missing in 5 other chunks)"


def test_two_duplicates_singular_chunk():
    findings = [_missing("rto-rpo"), _missing("rto-rpo")]
    out = va.dedupe_missing_findings(findings)
    assert len(out) == 1
    assert out[0]["Description"].endswith("(also missing in 1 other chunk)")


def test_mixed_types_only_missing_deduped():
    findings = [
        _missing("rto-rpo", description="m1"),
        {"Type": "Violation", "Issue": "v", "Description": "vd",
         "Principles": "rto-rpo", "Mandatory": True, "Category": "c"},
        _missing("rto-rpo", description="m2"),
        {"Type": "Deviation", "Issue": "d2", "Description": "dd",
         "Principles": "rto-rpo", "Mandatory": False, "Category": "c"},
    ]
    out = va.dedupe_missing_findings(findings)
    # Missing collapsed to 1; Violation + Deviation preserved
    types = [f["Type"] for f in out]
    assert types.count("Missing") == 1
    assert "Violation" in types and "Deviation" in types
    missing = next(f for f in out if f["Type"] == "Missing")
    assert missing["Description"] == "m1 (also missing in 1 other chunk)"


def test_empty_principles_never_merged():
    findings = [
        _missing("", description="gap-A"),
        _missing("   ", description="gap-B"),
        _missing("", description="gap-C"),
    ]
    out = va.dedupe_missing_findings(findings)
    assert len(out) == 3
    assert all("also missing in" not in (f.get("Description") or "") for f in out)


def test_case_and_whitespace_normalized_in_key():
    findings = [
        _missing("Resilience: RTO/RPO", description="m1"),
        _missing("  resilience: rto/rpo  ", description="m2"),
        _missing("RESILIENCE: RTO/RPO", description="m3"),
    ]
    out = va.dedupe_missing_findings(findings)
    assert len(out) == 1
    # Original casing preserved on survivor
    assert out[0]["Principles"] == "Resilience: RTO/RPO"
    assert out[0]["Description"] == "m1 (also missing in 2 other chunks)"


def test_error_findings_not_collapsed():
    findings = [
        {"Type": "Error", "Issue": "agent_call_failed", "Description": "e1",
         "Principles": "", "Mandatory": False, "Category": ""},
        {"Type": "Error", "Issue": "agent_call_failed", "Description": "e2",
         "Principles": "", "Mandatory": False, "Category": ""},
    ]
    out = va.dedupe_missing_findings(findings)
    assert out == findings


def test_order_preserved():
    findings = [
        _missing("p-a", description="a1"),
        _missing("p-b", description="b1"),
        _missing("p-a", description="a2"),
        _missing("p-c", description="c1"),
        _missing("p-b", description="b2"),
    ]
    out = va.dedupe_missing_findings(findings)
    principles_order = [f["Principles"] for f in out]
    assert principles_order == ["p-a", "p-b", "p-c"]


def test_idempotent():
    findings = [
        _missing("p-a", description="a1"),
        _missing("p-a", description="a2"),
        _missing("p-b", description="b1"),
    ]
    once = va.dedupe_missing_findings(findings)
    twice = va.dedupe_missing_findings(once)
    assert once == twice


def test_does_not_mutate_input():
    findings = [_missing("p-a", description="x"), _missing("p-a", description="y")]
    original_descs = [f["Description"] for f in findings]
    _ = va.dedupe_missing_findings(findings)
    assert [f["Description"] for f in findings] == original_descs


def test_mandatory_conflict_first_wins(caplog):
    findings = [
        _missing("p-a", description="m1", mandatory=True),
        _missing("p-a", description="m2", mandatory=False),
    ]
    out = va.dedupe_missing_findings(findings)
    assert len(out) == 1
    assert out[0]["Mandatory"] is True


def test_category_conflict_first_wins():
    findings = [
        _missing("p-a", description="m1", category="resilience"),
        _missing("p-a", description="m2", category="security"),
    ]
    out = va.dedupe_missing_findings(findings)
    assert len(out) == 1
    assert out[0]["Category"] == "resilience"


def test_non_dict_entry_passes_through():
    findings: list[Any] = [
        "not-a-dict",  # type: ignore[list-item]
        _missing("p-a", description="m1"),
        _missing("p-a", description="m2"),
    ]
    out = va.dedupe_missing_findings(findings)
    assert out[0] == "not-a-dict"
    missing = [f for f in out if isinstance(f, dict) and f.get("Type") == "Missing"]
    assert len(missing) == 1


# --- Integration: dedupe is wired into validate_arb_chunks ------------------

def test_validate_arb_chunks_applies_dedupe(monkeypatch):
    """End-to-end check that ``validate_arb_chunks`` runs the dedupe step."""

    # Stub chunker → 3 chunks.
    def fake_chunk(file_bytes, filename):
        return ["chunk-0", "chunk-1", "chunk-2"]

    # Each chunk yields the same Missing finding.
    async def fake_validate_single(cli, cfg, idx, chunk, failures):
        return [_missing("rto-rpo", description=f"from-{idx}")]

    monkeypatch.setattr(va, "_validate_single_chunk", fake_validate_single)

    # Patch the lazy chunker import inside validate_arb_chunks.
    import sys
    fake_mod = type(sys)("agents.asd_chunker")
    fake_mod.chunk_asd_document = fake_chunk  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents.asd_chunker", fake_mod)

    cfg = Config()
    out = asyncio.run(
        va.validate_arb_chunks(b"bytes", "f.pdf", cfg, client=object())
    )
    missing = [f for f in out if f.get("Type") == "Missing"]
    assert len(missing) == 1
    assert "also missing in 2 other chunks" in missing[0]["Description"]
