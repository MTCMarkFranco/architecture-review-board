"""Resolve Copilot Studio SharePoint file references via Graph + OBO.

Contract: MCP-SHAREPOINT-OBO (#91).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import McpConfig

logger = logging.getLogger("mcp_server.sharepoint")


class FileReferenceError(ValueError):
    """Raised when the Copilot Studio file reference JSON is unusable."""


class GraphOboError(RuntimeError):
    """OBO exchange failed (consent, audience, signature, etc.)."""


class GraphAccessError(RuntimeError):
    """Graph returned 403/404 for the calling user."""


class GraphThrottledError(RuntimeError):
    """Graph 429 — too many requests."""


@dataclass
class FileRef:
    name: str | None
    drive_id: str | None
    item_id: str | None
    site_id: str | None
    web_url: str | None

    def is_resolvable(self) -> bool:
        return bool(
            (self.drive_id and self.item_id)
            or (self.site_id and self.item_id)
            or self.web_url
        )


def parse_file_reference(payload: dict[str, Any]) -> FileRef:
    """Normalize the Copilot Studio file-reference JSON."""
    if not isinstance(payload, dict):
        raise FileReferenceError("file_reference must be a JSON object")

    inner = payload.get("reference") if isinstance(payload.get("reference"), dict) else payload

    name = payload.get("name") or inner.get("name")
    ref = FileRef(
        name=(name.strip() if isinstance(name, str) else None),
        drive_id=_str_or_none(inner.get("driveId") or inner.get("drive_id")),
        item_id=_str_or_none(inner.get("itemId") or inner.get("item_id")),
        site_id=_str_or_none(inner.get("siteId") or inner.get("site_id")),
        web_url=_str_or_none(inner.get("webUrl") or inner.get("web_url") or inner.get("url")),
    )
    if not ref.is_resolvable():
        raise FileReferenceError(
            "file_reference is ambiguous: provide one of "
            "(driveId+itemId), (siteId+itemId), or webUrl/sharing link."
        )
    return ref


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def acquire_graph_token_obo(user_assertion: str, cfg: McpConfig) -> str:
    """Exchange the incoming user JWT for a Graph access token via OBO."""
    import msal

    secret = os.getenv("ENTRA_API_CLIENT_SECRET", "").strip()
    if not secret:
        raise GraphOboError(
            "ENTRA_API_CLIENT_SECRET is not set. OBO requires a confidential "
            "client. Source it from Key Vault or App Service config."
        )

    authority = f"https://login.microsoftonline.com/{cfg.tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id=cfg.api_client_id,
        client_credential=secret,
        authority=authority,
    )
    scopes = cfg.graph_scopes.split()
    result = app.acquire_token_on_behalf_of(
        user_assertion=user_assertion,
        scopes=scopes,
    )
    if "access_token" not in result:
        err = str(result.get("error", "obo_failed"))
        desc = str(result.get("error_description") or result)
        if "consent" in (err + desc).lower() or err == "invalid_grant":
            raise GraphOboError(
                f"obo_consent_required: {desc}. Grant admin consent for the "
                f"requested Graph scopes ({cfg.graph_scopes})."
            )
        raise GraphOboError(f"{err}: {desc}")
    return result["access_token"]


_ALLOWED_EXTS = {".pdf", ".docx"}


def download_file(
    file_ref: FileRef,
    graph_token: str,
    cfg: McpConfig,
    http_client: httpx.Client | None = None,
) -> tuple[bytes, str, str]:
    """Resolve ``file_ref`` to a Graph drive-item and return its bytes."""
    filename = file_ref.name or ""
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext and ext not in _ALLOWED_EXTS:
            raise FileReferenceError(
                f"unsupported_file_type: {ext!r}; expected one of "
                f"{sorted(_ALLOWED_EXTS)}"
            )

    headers = {"Authorization": f"Bearer {graph_token}"}
    own = http_client is None
    client = http_client or httpx.Client(timeout=60.0)
    try:
        drive_item = _resolve_drive_item(file_ref, headers, cfg, client)
        item_id = drive_item.get("id")
        drive_id = (
            (drive_item.get("parentReference") or {}).get("driveId")
            or file_ref.drive_id
        )
        if not (item_id and drive_id):
            raise GraphAccessError(
                "Graph response did not include a resolvable drive-item id."
            )
        download_url = drive_item.get("@microsoft.graph.downloadUrl")
        if download_url:
            resp = _retrying_get(client, download_url, headers=None)
        else:
            url = f"{cfg.graph_base_url}/drives/{drive_id}/items/{item_id}/content"
            resp = _retrying_get(client, url, headers=headers)
        _raise_for_status(resp, context="drive-item content")
        content = resp.content
        if len(content) > cfg.max_body_bytes:
            raise FileReferenceError(
                f"413 Payload Too Large: file exceeds "
                f"MCP_MAX_BODY_BYTES={cfg.max_body_bytes}"
            )
        if not filename:
            filename = str(drive_item.get("name") or "downloaded.bin")
        content_type = (
            (drive_item.get("file") or {}).get("mimeType")
            or resp.headers.get("content-type", "application/octet-stream")
        )
        return content, filename, content_type
    finally:
        if own:
            client.close()


def _resolve_drive_item(
    ref: FileRef,
    headers: dict[str, str],
    cfg: McpConfig,
    client: httpx.Client,
) -> dict[str, Any]:
    if ref.drive_id and ref.item_id:
        url = f"{cfg.graph_base_url}/drives/{ref.drive_id}/items/{ref.item_id}"
        resp = _retrying_get(client, url, headers=headers)
        _raise_for_status(resp, context="drive-item lookup (drive+item)")
        return resp.json()
    if ref.site_id and ref.item_id:
        drive_url = f"{cfg.graph_base_url}/sites/{ref.site_id}/drive"
        drive_resp = _retrying_get(client, drive_url, headers=headers)
        _raise_for_status(drive_resp, context="site default drive")
        drive_id = drive_resp.json().get("id")
        if not drive_id:
            raise GraphAccessError("Site has no default drive.")
        item_url = f"{cfg.graph_base_url}/drives/{drive_id}/items/{ref.item_id}"
        item_resp = _retrying_get(client, item_url, headers=headers)
        _raise_for_status(item_resp, context="drive-item lookup (site+item)")
        return item_resp.json()
    if ref.web_url:
        encoded = _encode_sharing_url(ref.web_url)
        url = f"{cfg.graph_base_url}/shares/{encoded}/driveItem"
        resp = _retrying_get(client, url, headers=headers)
        _raise_for_status(resp, context="shares/driveItem")
        return resp.json()
    raise FileReferenceError(
        "Unreachable: parse_file_reference should have rejected this earlier."
    )


def _encode_sharing_url(web_url: str) -> str:
    """Graph sharing-link encoding: base64url with 'u!' prefix, no padding."""
    import base64
    b = base64.urlsafe_b64encode(web_url.encode("utf-8")).decode("ascii").rstrip("=")
    return "u!" + b


def _retrying_get(
    client: httpx.Client,
    url: str,
    headers: dict[str, str] | None,
    max_retries: int = 3,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.get(url, headers=headers, follow_redirects=True)
        except httpx.HTTPError as e:
            last_exc = e
            time.sleep(0.5 * (2**attempt))
            continue
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("retry-after", "1"))
            time.sleep(min(retry_after, 10.0))
            continue
        return resp
    if last_exc:
        raise GraphAccessError(f"Graph GET failed after retries: {last_exc}")
    raise GraphThrottledError(f"Graph throttled (429) for {url}")


def _raise_for_status(resp: httpx.Response, *, context: str) -> None:
    if resp.status_code in (401, 403):
        raise GraphAccessError(
            f"file_access_denied ({context}, HTTP {resp.status_code}): "
            f"the caller does not have access to this file."
        )
    if resp.status_code == 404:
        raise GraphAccessError(
            f"file_access_denied ({context}, HTTP 404): file not found or "
            "the caller has no permission to see it."
        )
    if resp.status_code == 429:
        raise GraphThrottledError(f"graph_throttled ({context})")
    if resp.status_code >= 400:
        raise GraphAccessError(
            f"Graph error ({context}, HTTP {resp.status_code}): {resp.text[:300]}"
        )
