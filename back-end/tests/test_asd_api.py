"""ASD API test suite (issue #19).

Drives ``/validatearb`` and ``/geniac`` through Flask's ``app.test_client()``
using ``sample_asd.docx``. No browser, no UI driver.

Tiers:
  - smoke (no marker): monkeypatches the orchestrator so the request/response
    contract can be verified without Azure access. Skips when sibling PRs
    (#16 sample doc + parse_arb_docx, #23 orchestrator) are not yet merged.
  - integration (``@pytest.mark.integration``): hits the real orchestrator
    against live Foundry/Search; skipped without credentials.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Smoke tier — monkeypatched, no Azure.
# ---------------------------------------------------------------------------

def _read_sample(sample_asd_path: Path) -> bytes:
    return sample_asd_path.read_bytes()


def test_validatearb_request_response_shape(flask_client, sample_asd_path, monkeypatch):
    """POST sample_asd.docx -> 200 with per-section findings (mocked orchestrator)."""
    fake_findings = {
        "Summary": [{"finding": "ok", "category": "general"}],
        "Solution Requirements": [{"finding": "review iam", "category": "Identity and Access"}],
        "Proposed Solution": [{"finding": "ok", "category": "Operational Excellence"}],
    }

    async def fake_validate_arb(arb):
        # Confirm the route actually parsed the docx before calling us.
        assert isinstance(arb, dict)
        assert "Introduction" in arb or "Summary" in arb
        return json.dumps(fake_findings)

    # The route imports ``validate_arb`` at module load; patch on the imported
    # symbol inside ``app`` so the route picks it up.
    import app as app_module  # type: ignore
    monkeypatch.setattr(app_module, "validate_arb", fake_validate_arb, raising=True)

    data = {"file": (io.BytesIO(_read_sample(sample_asd_path)), "sample_asd.docx")}
    resp = flask_client.post("/validatearb", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    payload = json.loads(resp.get_data(as_text=True))
    assert {"Summary", "Solution Requirements", "Proposed Solution"} <= set(payload.keys())
    for section, findings in payload.items():
        assert isinstance(findings, list) and findings, f"empty findings for {section}"
        for f in findings:
            assert "category" in f, f"finding missing category in {section}: {f}"


def test_geniac_returns_terraform(flask_client, sample_asd_path, monkeypatch):
    """POST sample_asd.docx -> 200 with non-empty Terraform-shaped string."""
    terraform_blob = (
        'terraform {\n  required_providers {\n    azurerm = {\n'
        '      source  = "hashicorp/azurerm"\n    }\n  }\n}\n'
        'resource "azurerm_resource_group" "rg" {\n'
        '  name     = "aurora-rg"\n  location = "canadacentral"\n}\n'
    )

    async def fake_generate_iac(arb):
        assert isinstance(arb, dict)
        return terraform_blob

    import app as app_module  # type: ignore
    monkeypatch.setattr(app_module, "generate_iac", fake_generate_iac, raising=True)

    data = {"file": (io.BytesIO(_read_sample(sample_asd_path)), "sample_asd.docx")}
    resp = flask_client.post("/geniac", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    body = resp.get_data(as_text=True)
    assert "terraform" in body.lower()
    assert "resource" in body.lower()
    assert len(body.strip()) > 50


def test_validatearb_rejects_missing_file(flask_client):
    """Sanity check: no ``file`` part -> 400."""
    resp = flask_client.post("/validatearb", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Integration tier — live orchestrator.
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_validatearb_live(flask_client, sample_asd_path):
    """Live round-trip; requires Foundry + Search to be configured."""
    import os
    if not os.getenv("FOUNDRY_ENDPOINT") or not os.getenv("AZURE_SEARCH_ENDPOINT"):
        pytest.skip("FOUNDRY_ENDPOINT / AZURE_SEARCH_ENDPOINT not set")

    data = {"file": (io.BytesIO(sample_asd_path.read_bytes()), "sample_asd.docx")}
    resp = flask_client.post("/validatearb", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    payload = json.loads(resp.get_data(as_text=True))
    assert isinstance(payload, dict) and payload
    for section, findings in payload.items():
        assert isinstance(findings, list)
        for f in findings:
            assert "category" in f, f"finding missing category in {section}"


@pytest.mark.integration
def test_geniac_live(flask_client, sample_asd_path):
    import os
    if not os.getenv("FOUNDRY_ENDPOINT"):
        pytest.skip("FOUNDRY_ENDPOINT not set")

    data = {"file": (io.BytesIO(sample_asd_path.read_bytes()), "sample_asd.docx")}
    resp = flask_client.post("/geniac", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    body = resp.get_data(as_text=True)
    assert "terraform" in body.lower() or "resource" in body.lower()
