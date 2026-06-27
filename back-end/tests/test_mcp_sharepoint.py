"""Unit tests for mcp_server.sharepoint (reference parsing + OBO + Graph mocked)."""

from __future__ import annotations

import httpx
import pytest

from mcp_server.config import McpConfig
from mcp_server.sharepoint import (
    FileRef,
    FileReferenceError,
    GraphAccessError,
    GraphOboError,
    _encode_sharing_url,
    acquire_graph_token_obo,
    download_file,
    parse_file_reference,
)


@pytest.fixture()
def cfg() -> McpConfig:
    c = McpConfig()
    c.tenant_id = "tenant"
    c.api_client_id = "client"
    c.graph_base_url = "https://graph.microsoft.com/v1.0"
    return c


# ---------------------------------------------------------------------------
# parse_file_reference
# ---------------------------------------------------------------------------

def test_parse_flat_drive_item():
    ref = parse_file_reference({"name": "x.docx", "driveId": "D", "itemId": "I"})
    assert ref.drive_id == "D" and ref.item_id == "I"
    assert ref.is_resolvable()


def test_parse_nested_reference():
    ref = parse_file_reference({
        "name": "x.pdf",
        "reference": {"driveId": "D2", "itemId": "I2"},
    })
    assert ref.drive_id == "D2"


def test_parse_site_item():
    ref = parse_file_reference({"name": "x.pdf", "siteId": "S", "itemId": "I"})
    assert ref.site_id == "S"
    assert ref.is_resolvable()


def test_parse_web_url():
    ref = parse_file_reference({"name": "x.pdf",
                                "webUrl": "https://contoso.sharepoint.com/x.pdf"})
    assert ref.web_url.startswith("https://")


def test_parse_ambiguous_raises():
    with pytest.raises(FileReferenceError):
        parse_file_reference({"name": "x"})


def test_parse_non_dict_raises():
    with pytest.raises(FileReferenceError):
        parse_file_reference("not-a-dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _encode_sharing_url
# ---------------------------------------------------------------------------

def test_encode_sharing_url_prefixes_u_and_strips_padding():
    out = _encode_sharing_url("https://contoso.sharepoint.com/sites/x")
    assert out.startswith("u!")
    assert "=" not in out


# ---------------------------------------------------------------------------
# acquire_graph_token_obo (MSAL mocked)
# ---------------------------------------------------------------------------

def test_obo_requires_secret(monkeypatch, cfg):
    monkeypatch.delenv("ENTRA_API_CLIENT_SECRET", raising=False)
    with pytest.raises(GraphOboError) as ei:
        acquire_graph_token_obo("user-jwt", cfg)
    assert "ENTRA_API_CLIENT_SECRET" in str(ei.value)


def test_obo_consent_required(monkeypatch, cfg):
    monkeypatch.setenv("ENTRA_API_CLIENT_SECRET", "fake-secret")

    class _FakeApp:
        def __init__(self, *a, **kw):
            pass

        def acquire_token_on_behalf_of(self, *, user_assertion, scopes):
            return {"error": "invalid_grant",
                    "error_description": "AADSTS65001: consent required"}

    monkeypatch.setattr("msal.ConfidentialClientApplication", _FakeApp)
    with pytest.raises(GraphOboError) as ei:
        acquire_graph_token_obo("user-jwt", cfg)
    assert "obo_consent_required" in str(ei.value)


def test_obo_success(monkeypatch, cfg):
    monkeypatch.setenv("ENTRA_API_CLIENT_SECRET", "fake-secret")

    class _FakeApp:
        def __init__(self, *a, **kw):
            pass

        def acquire_token_on_behalf_of(self, *, user_assertion, scopes):
            return {"access_token": "graph-token-123", "expires_in": 3600}

    monkeypatch.setattr("msal.ConfidentialClientApplication", _FakeApp)
    token = acquire_graph_token_obo("user-jwt", cfg)
    assert token == "graph-token-123"


# ---------------------------------------------------------------------------
# download_file (Graph mocked via httpx MockTransport)
# ---------------------------------------------------------------------------

def _mock_client(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        for matcher, response in routes:
            if matcher(request):
                return response
        return httpx.Response(404, content=b'{"error":"unmatched"}')
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_via_drive_item(cfg):
    item_json = {
        "id": "ITEM-1",
        "name": "sample-asd.pdf",
        "parentReference": {"driveId": "DRIVE-1"},
        "file": {"mimeType": "application/pdf"},
    }
    routes = [
        (lambda r: r.url.path.endswith("/drives/DRIVE-1/items/ITEM-1"),
         httpx.Response(200, json=item_json)),
        (lambda r: r.url.path.endswith("/drives/DRIVE-1/items/ITEM-1/content"),
         httpx.Response(200, content=b"%PDF-1.4 fake")),
    ]
    client = _mock_client(routes)
    ref = FileRef(name="sample-asd.pdf", drive_id="DRIVE-1", item_id="ITEM-1",
                  site_id=None, web_url=None)
    data, filename, ctype = download_file(ref, "graph-tok", cfg, http_client=client)
    assert data.startswith(b"%PDF")
    assert filename == "sample-asd.pdf"
    assert "pdf" in ctype


def test_download_via_web_url(cfg):
    item_json = {
        "id": "ITEM-X",
        "name": "linked.docx",
        "parentReference": {"driveId": "DRIVE-X"},
        "@microsoft.graph.downloadUrl": "https://download.example/linked.docx",
        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    }
    routes = [
        (lambda r: "/shares/" in r.url.path and r.url.path.endswith("/driveItem"),
         httpx.Response(200, json=item_json)),
        (lambda r: r.url.host == "download.example",
         httpx.Response(200, content=b"PK\x03\x04 fake docx")),
    ]
    client = _mock_client(routes)
    ref = FileRef(name=None, drive_id=None, item_id=None, site_id=None,
                  web_url="https://contoso.sharepoint.com/Shared%20Documents/linked.docx")
    data, filename, _ctype = download_file(ref, "graph-tok", cfg, http_client=client)
    assert data.startswith(b"PK")
    assert filename == "linked.docx"


def test_download_unauthorized_surfaces_access_denied(cfg):
    routes = [
        (lambda r: True, httpx.Response(403, content=b'{"error":"denied"}')),
    ]
    client = _mock_client(routes)
    ref = FileRef(name="x.pdf", drive_id="D", item_id="I", site_id=None, web_url=None)
    with pytest.raises(GraphAccessError) as ei:
        download_file(ref, "tok", cfg, http_client=client)
    assert "file_access_denied" in str(ei.value)


def test_download_unsupported_extension_rejected(cfg):
    ref = FileRef(name="malware.exe", drive_id="D", item_id="I",
                  site_id=None, web_url=None)
    with pytest.raises(FileReferenceError) as ei:
        download_file(ref, "tok", cfg, http_client=_mock_client([]))
    assert "unsupported_file_type" in str(ei.value)
