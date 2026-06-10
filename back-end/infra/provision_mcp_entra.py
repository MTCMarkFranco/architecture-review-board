"""Provision the Entra app registration that protects the MCP server.

Idempotent helper for contracts MCP-SERVER-ENTRA (#90) and
MCP-SHAREPOINT-OBO (#91).

What this script does:

  1. Creates (or reuses) an Entra app registration named
     ``arb-bot-mcp-api`` configured as a confidential web/API app.
  2. Exposes a delegated scope (default ``ARB.Invoke``) on the API.
  3. Sets the ``identifierUris`` to ``api://<appId>`` so tokens can target
     that audience.
  4. Adds delegated Microsoft Graph permissions ``Files.Read.All`` and
     ``Sites.Read.All`` (for OBO → SharePoint download).
  5. Adds a Service Principal in the tenant so the app can be granted
     admin consent.
  6. Attempts admin-consent grant for the Graph scopes if the running
     identity has rights; otherwise prints the consent URL.
  7. Creates a client secret if requested with ``--issue-secret`` and
     prints it ONCE to stdout (not written to disk). The user must store
     it in Key Vault / App Service config.
  8. Writes the non-secret IDs into ``back-end/.env.example``.

All Azure access uses the logged-in ``az`` session (Azure CLI). Run::

    az login
    python -m infra.provision_mcp_entra
    python -m infra.provision_mcp_entra --dry-run
    python -m infra.provision_mcp_entra --issue-secret
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("provision_mcp_entra")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


# On Windows ``az`` is a .cmd shim that subprocess.run cannot exec directly.
_AZ_PATH = shutil.which("az") or shutil.which("az.cmd") or "az"


# Microsoft Graph application id (well-known constant).
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"

# Delegated permission ids on Microsoft Graph (well-known constants).
# Source: https://learn.microsoft.com/en-us/graph/permissions-reference
GRAPH_DELEGATED_PERMISSIONS = {
    "Files.Read.All": "df85f4d6-205c-4ac5-a5ea-6bf408dba283",
    "Sites.Read.All": "205e70e5-aba6-4c52-a976-6d2d46c48043",
    "User.Read": "e1fe6dd8-ba31-4d61-89e7-88639da4683d",
    "offline_access": "7427e0e9-2fba-42fe-b0c0-848c9e6a8182",
}

DEFAULT_APP_NAME = os.getenv("MCP_APP_NAME", "arb-bot-mcp-api")
DEFAULT_SCOPE = os.getenv("ENTRA_REQUIRED_SCOPE", "ARB.Invoke")

REPO_BACK_END = Path(__file__).resolve().parents[1]
ENV_EXAMPLE_PATH = REPO_BACK_END / ".env.example"


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def _az(*args: str, capture: bool = True, allow_fail: bool = False) -> dict | list | str | None:
    cmd = [_AZ_PATH, *args]
    log.debug("az: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            check=not allow_fail,
            capture_output=capture,
            text=True,
            shell=False,
        )
    except FileNotFoundError:
        log.error("Azure CLI ('az') not found on PATH. Install from https://aka.ms/azure-cli")
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        log.error("az command failed: %s\nstderr: %s", " ".join(cmd), e.stderr.strip())
        raise
    if not capture:
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def _az_rest_patch(url: str, body: dict) -> None:
    """PATCH a Graph URL via `az rest`, passing the body as a file (Windows-safe)."""
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8",
    ) as fh:
        json.dump(body, fh)
        body_path = fh.name
    try:
        _az(
            "rest", "--method", "PATCH",
            "--url", url,
            "--headers", "Content-Type=application/json",
            "--body", f"@{body_path}",
        )
    finally:
        try:
            os.unlink(body_path)
        except OSError:
            pass


def _tenant_and_subscription() -> tuple[str, str]:
    info = _az("account", "show")
    if not isinstance(info, dict):
        log.error("`az account show` returned no JSON. Run `az login`.")
        sys.exit(2)
    return info["tenantId"], info["id"]
    info = _az("account", "show")
    if not isinstance(info, dict):
        log.error("`az account show` returned no JSON. Run `az login`.")
        sys.exit(2)
    return info["tenantId"], info["id"]


# ---------------------------------------------------------------------------
# App registration
# ---------------------------------------------------------------------------

def _find_or_create_app(app_name: str, dry_run: bool) -> dict[str, Any]:
    """Idempotent: returns the existing app or creates a new one."""
    apps = _az("ad", "app", "list", "--display-name", app_name)
    if isinstance(apps, list) and apps:
        log.info("Reusing existing app registration: %s (appId=%s)",
                 app_name, apps[0]["appId"])
        return apps[0]
    if dry_run:
        log.info("[DRY RUN] Would create app registration: %s", app_name)
        return {"appId": "<dry-run>", "id": "<dry-run>"}
    log.info("Creating app registration: %s", app_name)
    app = _az(
        "ad", "app", "create",
        "--display-name", app_name,
        "--sign-in-audience", "AzureADMyOrg",
    )
    assert isinstance(app, dict)
    return app


def _ensure_identifier_uri(app: dict[str, Any], dry_run: bool) -> str:
    """Set identifierUris to api://<appId> if not already present."""
    expected = f"api://{app['appId']}"
    uris = app.get("identifierUris") or []
    if expected in uris:
        log.info("identifierUris already includes %s", expected)
        return expected
    if dry_run:
        log.info("[DRY RUN] Would set identifierUris to %s", expected)
        return expected
    log.info("Setting identifierUris to %s", expected)
    _az(
        "ad", "app", "update",
        "--id", app["appId"],
        "--identifier-uris", expected,
    )
    return expected


def _ensure_scope_exposed(app: dict[str, Any], scope_name: str, dry_run: bool) -> str:
    """Ensure the OAuth2 delegated scope exists. Returns the scope value string."""
    if dry_run:
        log.info("[DRY RUN] Would ensure scope %s exposed on %s", scope_name, app.get("appId"))
        return scope_name
    # Re-read the app to get current api.oauth2PermissionScopes (the
    # `az ad app list` projection sometimes omits this).
    full = _az("ad", "app", "show", "--id", app["appId"])
    assert isinstance(full, dict)
    scopes = (full.get("api") or {}).get("oauth2PermissionScopes") or []
    for s in scopes:
        if s.get("value") == scope_name and s.get("isEnabled", False):
            log.info("Scope %s already exposed (id=%s)", scope_name, s["id"])
            return scope_name
    import uuid
    new_scope_id = str(uuid.uuid4())
    new_scope = {
        "adminConsentDescription": f"Allow the application to invoke ARB Bot ({scope_name}) on behalf of the signed-in user.",
        "adminConsentDisplayName": f"Invoke ARB Bot ({scope_name})",
        "id": new_scope_id,
        "isEnabled": True,
        "type": "User",
        "userConsentDescription": f"Allow the app to invoke ARB Bot ({scope_name}) on your behalf.",
        "userConsentDisplayName": f"Invoke ARB Bot ({scope_name})",
        "value": scope_name,
    }
    updated_scopes = scopes + [new_scope]
    api_body = {"oauth2PermissionScopes": updated_scopes}
    if dry_run:
        log.info("[DRY RUN] Would expose scope %s on %s", scope_name, app["appId"])
        return scope_name
    log.info("Exposing scope %s on %s", scope_name, app["appId"])
    _az_rest_patch(
        f"https://graph.microsoft.com/v1.0/applications/{full['id']}",
        {"api": api_body},
    )
    return scope_name


def _ensure_graph_permissions(app: dict[str, Any], permissions: list[str], dry_run: bool) -> None:
    """Add the requested delegated Graph permissions (idempotent)."""
    if dry_run:
        log.info("[DRY RUN] Would ensure delegated Graph permissions on %s: %s",
                 app.get("appId"), ", ".join(permissions))
        return
    full = _az("ad", "app", "show", "--id", app["appId"])
    assert isinstance(full, dict)
    existing = full.get("requiredResourceAccess") or []

    # Find existing Graph entry.
    graph_entry: dict[str, Any] | None = None
    other_entries: list[dict[str, Any]] = []
    for e in existing:
        if e.get("resourceAppId") == GRAPH_APP_ID:
            graph_entry = e
        else:
            other_entries.append(e)
    if graph_entry is None:
        graph_entry = {"resourceAppId": GRAPH_APP_ID, "resourceAccess": []}
    existing_ids = {ra["id"] for ra in graph_entry.get("resourceAccess", [])}

    to_add = []
    for perm in permissions:
        pid = GRAPH_DELEGATED_PERMISSIONS.get(perm)
        if pid is None:
            log.warning("Unknown Graph permission %r — skipping", perm)
            continue
        if pid in existing_ids:
            log.info("Graph permission %s already requested", perm)
            continue
        to_add.append({"id": pid, "type": "Scope"})
        log.info("Adding Graph permission %s (delegated)", perm)
    if not to_add and graph_entry.get("resourceAccess"):
        return
    graph_entry["resourceAccess"] = list(graph_entry.get("resourceAccess", [])) + to_add
    body = {"requiredResourceAccess": other_entries + [graph_entry]}
    if dry_run:
        log.info("[DRY RUN] Would update requiredResourceAccess for %s", app["appId"])
        return
    _az_rest_patch(
        f"https://graph.microsoft.com/v1.0/applications/{full['id']}",
        body,
    )


def _ensure_service_principal(app_id: str, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        log.info("[DRY RUN] Would ensure service principal for %s", app_id)
        return {"id": "<dry-run>"}
    sps = _az("ad", "sp", "list", "--filter", f"appId eq '{app_id}'")
    if isinstance(sps, list) and sps:
        log.info("Service principal already exists for %s", app_id)
        return sps[0]
    if dry_run:
        log.info("[DRY RUN] Would create service principal for %s", app_id)
        return {"id": "<dry-run>"}
    log.info("Creating service principal for %s", app_id)
    sp = _az("ad", "sp", "create", "--id", app_id)
    assert isinstance(sp, dict)
    return sp


def _attempt_admin_consent(app_id: str, tenant_id: str, dry_run: bool) -> bool:
    """Try `az ad app permission admin-consent`. Returns True if granted."""
    if dry_run:
        log.info("[DRY RUN] Would attempt admin-consent grant for %s", app_id)
        return False
    log.info("Attempting admin-consent grant for %s …", app_id)
    try:
        _az("ad", "app", "permission", "admin-consent", "--id", app_id,
            allow_fail=False)
    except subprocess.CalledProcessError:
        consent_url = (
            f"https://login.microsoftonline.com/{tenant_id}/adminconsent"
            f"?client_id={app_id}"
        )
        log.warning(
            "Admin-consent grant failed (your identity may lack Application.ReadWrite.All "
            "or tenant-admin rights). Have a tenant admin click this URL:\n  %s",
            consent_url,
        )
        return False
    log.info("Admin-consent grant succeeded.")
    return True


def _issue_client_secret(app_id: str, dry_run: bool) -> str | None:
    if dry_run:
        log.info("[DRY RUN] Would issue a new client secret for %s", app_id)
        return None
    log.warning(
        "Issuing a new client secret. STORE IT IN KEY VAULT IMMEDIATELY — "
        "it will NOT be written to disk and CANNOT be re-displayed."
    )
    cred = _az(
        "ad", "app", "credential", "reset",
        "--id", app_id,
        "--display-name", "arb-bot-mcp-obo",
        "--years", "1",
        "--append",
    )
    assert isinstance(cred, dict)
    return cred.get("password")


# ---------------------------------------------------------------------------
# .env.example writer
# ---------------------------------------------------------------------------

def _update_env_example(
    *, app_id: str, audience: str, scope: str, tenant_id: str, dry_run: bool,
) -> None:
    if dry_run:
        log.info("[DRY RUN] Would update %s with new MCP/Entra IDs.", ENV_EXAMPLE_PATH)
        return
    existing = ENV_EXAMPLE_PATH.read_text(encoding="utf-8") if ENV_EXAMPLE_PATH.exists() else ""
    additions = {
        "MCP_SERVER_NAME": os.getenv("MCP_SERVER_NAME", "arb-bot-mcp"),
        "MCP_ROUTE": "/api/mcp",
        "ENTRA_TENANT_ID": tenant_id,
        "ENTRA_API_CLIENT_ID": app_id,
        "ENTRA_API_AUDIENCE": audience,
        "ENTRA_REQUIRED_SCOPE": scope,
        "GRAPH_SCOPES": "Files.Read.All Sites.Read.All",
        # Note: ENTRA_API_CLIENT_SECRET is intentionally NOT written here.
    }
    lines = existing.splitlines()
    line_keys = {ln.split("=", 1)[0]: i for i, ln in enumerate(lines) if "=" in ln and not ln.lstrip().startswith("#")}
    for k, v in additions.items():
        if k in line_keys:
            lines[line_keys[k]] = f"{k}={v}"
        else:
            lines.append(f"{k}={v}")
    if "# --- MCP / Entra ---" not in existing:
        lines.append("# --- MCP / Entra ---")
        lines.append("# ENTRA_API_CLIENT_SECRET=  # SOURCE FROM KEY VAULT, NEVER COMMIT")
    ENV_EXAMPLE_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    log.info("Updated %s", ENV_EXAMPLE_PATH)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME,
                        help=f"Entra app registration display name (default: {DEFAULT_APP_NAME})")
    parser.add_argument("--scope", default=DEFAULT_SCOPE,
                        help=f"Delegated scope name (default: {DEFAULT_SCOPE})")
    parser.add_argument("--graph-permissions", nargs="+",
                        default=["Files.Read.All", "Sites.Read.All", "User.Read", "offline_access"],
                        help="Delegated Graph permissions to add")
    parser.add_argument("--issue-secret", action="store_true",
                        help="Issue a new client secret (prints it ONCE).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan only — no Azure writes.")
    args = parser.parse_args()

    tenant_id, subscription_id = _tenant_and_subscription()
    log.info("Tenant: %s | Subscription: %s", tenant_id, subscription_id)

    app = _find_or_create_app(args.app_name, args.dry_run)
    audience = _ensure_identifier_uri(app, args.dry_run)
    scope = _ensure_scope_exposed(app, args.scope, args.dry_run)
    _ensure_graph_permissions(app, args.graph_permissions, args.dry_run)
    _ensure_service_principal(app["appId"], args.dry_run)
    _attempt_admin_consent(app["appId"], tenant_id, args.dry_run)

    _update_env_example(
        app_id=app["appId"],
        audience=audience,
        scope=scope,
        tenant_id=tenant_id,
        dry_run=args.dry_run,
    )

    if args.issue_secret:
        secret = _issue_client_secret(app["appId"], args.dry_run)
        if secret:
            print()
            print("=" * 72)
            print(" ENTRA_API_CLIENT_SECRET (store in Key Vault NOW — shown once):")
            print(f"   {secret}")
            print("=" * 72)
            print()

    log.info("Done. Summary:")
    log.info("  ENTRA_TENANT_ID      = %s", tenant_id)
    log.info("  ENTRA_API_CLIENT_ID  = %s", app["appId"])
    log.info("  ENTRA_API_AUDIENCE   = %s", audience)
    log.info("  ENTRA_REQUIRED_SCOPE = %s", scope)
    log.info("Reminder: set ENTRA_API_CLIENT_SECRET (from Key Vault) in the runtime env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
