"""ASD API smoke tests for the post-#23 MAF orchestrator (issue #37).

Rewrites the pre-#23 monkeypatches of ``app.validate_arb`` / ``app.generate_iac``
to target the current orchestrator: ``agents.orchestrator.ArbWorkflow.validate``
and ``.iac``. Response-shape assertions follow ``prompt-contracts/TEST-ASD.md``
(``/validatearb`` → ``list[dict]`` with Type/Issue/Description/Principles/Mandatory;
``/geniac`` → ``list[str]`` of Terraform blocks).

Tiers:
  - smoke (no marker): fully offline. Monkeypatches the parser AND the
    orchestrator so neither python-docx, sample_asd.docx, nor Azure is needed.
  - integration (``@pytest.mark.integration``): hits the real orchestrator
    against live Foundry/Search; skipped without credentials.
"""

from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Smoke tier — fully offline. Parser + orchestrator are both stubbed.
# ---------------------------------------------------------------------------

_STUB_ARB = {
    "Introduction": "Project Aurora",
    "Proposed Solution": {"Proposed New Architecture": "EKS on AWS"},
    "EC2 Sizing/Specifications": [{"Environment": "prod"}],
}

_STUB_FINDINGS = [
    {
        "Type": "Security",
        "Issue": "Public S3 bucket",
        "Description": "Bucket policy allows public read.",
        "Principles": ["Identity and Access Management"],
        "Mandatory": True,
    },
    {
        "Type": "Cost",
        "Issue": "Oversized EC2",
        "Description": "m5.4xlarge for a 10 rps workload.",
        "Principles": ["Cost Management and Tagging"],
        "Mandatory": False,
    },
]

_STUB_IAC = [
    'resource "aws_s3_bucket" "logs" {\n  bucket = "aurora-logs"\n}\n',
    'resource "aws_instance" "web" {\n  ami = "ami-123"\n}\n',
]


@pytest.fixture
def stub_orchestrator(monkeypatch):
    """Patch ArbWorkflow.validate/.iac AND the parser; reset the cached workflow."""
    from agents.orchestrator import ArbWorkflow
    import app as app_module

    async def _validate(self, arb):  # noqa: ARG001
        assert isinstance(arb, dict)
        return list(_STUB_FINDINGS)

    async def _iac(self, arb):  # noqa: ARG001
        assert isinstance(arb, dict)
        return list(_STUB_IAC)

    monkeypatch.setattr(ArbWorkflow, "validate", _validate, raising=True)
    monkeypatch.setattr(ArbWorkflow, "iac", _iac, raising=True)
    monkeypatch.setattr(
        app_module, "_parse_uploaded", lambda _f: dict(_STUB_ARB), raising=True
    )
    # Force a fresh workflow so any prior cached instance does not leak state.
    monkeypatch.setattr(app_module, "_workflow", None, raising=False)


def _post(client, route: str, filename: str = "sample_asd.docx"):
    data = {"file": (io.BytesIO(b"stub-docx-bytes"), filename)}
    return client.post(route, data=data, content_type="multipart/form-data")


def test_validatearb_returns_list_of_findings(flask_client, stub_orchestrator):
    """/validatearb → 200 with list[dict] matching the MAF contract."""
    resp = _post(flask_client, "/validatearb")
    assert resp.status_code == 200, resp.data
    payload = json.loads(resp.get_data(as_text=True))
    assert isinstance(payload, list) and payload, "expected non-empty list"
    for f in payload:
        assert isinstance(f, dict)
        for key in ("Type", "Issue", "Description", "Principles", "Mandatory"):
            assert key in f, f"finding missing {key}: {f}"
    assert resp.headers.get("Content-Type", "").startswith("application/json")


def test_geniac_returns_list_of_terraform_strings(flask_client, stub_orchestrator):
    """/geniac → 200 with list[str] of Terraform blocks."""
    resp = _post(flask_client, "/geniac")
    assert resp.status_code == 200, resp.data
    payload = json.loads(resp.get_data(as_text=True))
    assert isinstance(payload, list) and payload, "expected non-empty list"
    for item in payload:
        assert isinstance(item, str)
        assert 'resource "aws_' in item, f"missing aws resource block: {item!r}"


def test_validatearb_rejects_missing_file(flask_client):
    """No ``file`` part → 400."""
    resp = flask_client.post(
        "/validatearb", data={}, content_type="multipart/form-data"
    )
    assert resp.status_code == 400


def test_orchestrator_emits_correlation_id(
    flask_client, stub_orchestrator, caplog, monkeypatch
):
    """ArbWorkflow.validate must log a correlation id (Observability gate, #37)."""
    # The real ArbWorkflow logs ``[ARB:<cid>] validate(sections) start`` /
    # ``[ARB:<cid>] validate(chunks) start`` (the API uses chunks; sections is
    # the legacy programmatic path) and ``... ok in ...``. Our stub bypasses
    # that, so invoke the real validate method's log path directly to assert
    # the contract holds on the unstubbed code.
    import importlib

    orch_mod = importlib.import_module("agents.orchestrator")
    caplog.set_level(logging.INFO, logger=orch_mod.__name__)

    # Capture the format string of the start-of-validate log call by inspecting
    # the source — the contract is "[ARB:<cid>] <stage> start" where cid is an
    # 8-char hex correlation id, for every stage method.
    src = Path(orch_mod.__file__).read_text(encoding="utf-8")
    assert 'uuid.uuid4().hex[:8]' in src, "correlation id generator missing"
    assert '[ARB:%s] validate(sections) start' in src, "validate(sections) log missing cid"
    assert '[ARB:%s] validate(chunks) start' in src, "validate(chunks) log missing cid"
    assert '[ARB:%s] iac start' in src, "iac start log missing cid"

    # And the API path returns 200, confirming the stubbed pipeline runs end-to-end.
    resp = _post(flask_client, "/validatearb")
    assert resp.status_code == 200

    # Run the real validate() against a stub client to actually emit a log line
    # and assert the [ARB:<cid>] prefix appears.
    import asyncio

    from agents.config import Config

    class _StubAgentsClient:
        pass

    class _PassthroughBreaker:
        async def call(self, fn):
            return await fn()

        def record_success(self):
            pass

        def record_failure(self):
            pass

    # Re-import to bypass the class-level monkeypatch from stub_orchestrator.
    importlib.reload(orch_mod)
    caplog.clear()
    caplog.set_level(logging.INFO, logger=orch_mod.__name__)

    async def _fake_validate_arb_sections(arb, cfg, client):  # noqa: ARG001
        return [{"Type": "x", "Issue": "y", "Description": "z",
                 "Principles": [], "Mandatory": False}]

    import agents.validate_agent as va
    monkeypatch.setattr(va, "validate_arb_sections", _fake_validate_arb_sections)
    monkeypatch.setattr(orch_mod, "validate_arb_sections", _fake_validate_arb_sections)

    wf = orch_mod.ArbWorkflow(config=Config(), client=_StubAgentsClient())
    asyncio.run(wf.validate(dict(_STUB_ARB)))

    cid_pattern = re.compile(r"\[ARB:[0-9a-f]{8}\] validate")
    matches = [r for r in caplog.records if cid_pattern.search(r.getMessage())]
    assert matches, (
        f"orchestrator did not emit [ARB:<cid>] log; saw: "
        f"{[r.getMessage() for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Integration tier — live orchestrator. Skipped without credentials.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_validatearb_live(flask_client, sample_asd_path):
    """Live round-trip; requires Foundry + Search to be configured."""
    import os

    if not os.getenv("FOUNDRY_ENDPOINT") or not os.getenv("AZURE_SEARCH_ENDPOINT"):
        pytest.skip("FOUNDRY_ENDPOINT / AZURE_SEARCH_ENDPOINT not set")

    data = {"file": (io.BytesIO(sample_asd_path.read_bytes()), "sample_asd.docx")}
    resp = flask_client.post(
        "/validatearb", data=data, content_type="multipart/form-data"
    )
    assert resp.status_code == 200, resp.data
    payload = json.loads(resp.get_data(as_text=True))
    assert isinstance(payload, list)
    for f in payload:
        for key in ("Type", "Issue", "Description", "Principles", "Mandatory"):
            assert key in f


@pytest.mark.integration
def test_geniac_live(flask_client, sample_asd_path):
    import os

    if not os.getenv("FOUNDRY_ENDPOINT"):
        pytest.skip("FOUNDRY_ENDPOINT not set")

    data = {"file": (io.BytesIO(sample_asd_path.read_bytes()), "sample_asd.docx")}
    resp = flask_client.post(
        "/geniac", data=data, content_type="multipart/form-data"
    )
    assert resp.status_code == 200, resp.data
    payload = json.loads(resp.get_data(as_text=True))
    assert isinstance(payload, list)
    for item in payload:
        assert isinstance(item, str)
