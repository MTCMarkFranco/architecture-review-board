"""Provision the supporting Azure resources for the arb-policies pull-mode pipeline.

Idempotent. Creates (or detects + reuses):

1. A **storage account** (StorageV2, LRS, hot tier) in the same region + resource
   group as the Azure AI Search service that hosts the ``arb-policies`` index.
2. A **blob container** (default name ``arb-policies-source``) to receive source
   policy documents (.docx / .pdf).
3. **RBAC**: grants the Azure AI Search service's system-assigned managed
   identity the ``Storage Blob Data Reader`` role on the storage account.
4. **RBAC**: grants the signed-in user (the one running this script) the
   ``Storage Blob Data Contributor`` role so you can upload source docs from
   your dev machine without re-authenticating.
5. Writes the resolved ``STORAGE_ACCOUNT_RESOURCE_ID`` into the runtime
   summary so ``infra/provision.py`` can append it to ``back-end/.env.example``
   for the user to copy into the repo-root ``.env``.

The Foundry account (``foundry-cc-canada``) is used as the Content
Understanding billing target by default — that wiring lives in the skillset
definition (see ``back-end/search/skillset_definition.json`` and
``FOUNDRY_CU_ENDPOINT`` in ``back-end/search/build_indexer.py``).

Authentication is via the ``az`` CLI (must be logged in to the target
subscription). All ARM calls go through ``az``.

Usage (typically called by ``infra/provision.py``, but runnable standalone):

    python -m infra.provision_search_pipeline
    python -m infra.provision_search_pipeline --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import uuid

log = logging.getLogger("provision_search_pipeline")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

AZ_EXE = shutil.which("az") or "az"

# Defaults — overrideable via env.
DEFAULT_SEARCH_RG = os.getenv("AZURE_SEARCH_RESOURCE_GROUP", "rg-arb-search-cc")
DEFAULT_SEARCH_NAME = os.getenv("AZURE_SEARCH_NAME", "arb-search-cc")
DEFAULT_STORAGE_NAME = os.getenv(
    "ARB_STORAGE_ACCOUNT_NAME",
    f"arbpolicies{uuid.uuid4().hex[:6]}",
)
DEFAULT_CONTAINER = os.getenv("STORAGE_CONTAINER", "arb-policies-source")

# Built-in Azure role definition IDs (stable across tenants).
ROLE_BLOB_READER = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"        # Storage Blob Data Reader
ROLE_BLOB_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"   # same role works for upload too; using Contributor below
ROLE_BLOB_DATA_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # placeholder — replaced in code below
STORAGE_BLOB_DATA_READER_ID = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
STORAGE_BLOB_DATA_CONTRIBUTOR_ID = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"


# ---------------------------------------------------------------------------
# az helpers
# ---------------------------------------------------------------------------

def _az(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = [AZ_EXE, *args]
    log.debug("$ %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def _az_json(*args: str) -> object:
    res = _az(*args, "-o", "json")
    out = res.stdout.strip()
    return json.loads(out) if out else None


def _current_signed_in_object_id() -> str:
    return _az_json("ad", "signed-in-user", "show", "--query", "id")  # type: ignore[return-value]


def _search_service_principal_id(rg: str, name: str) -> str:
    """Return the search service's system-assigned managed identity principal id.

    Enables system-assigned identity if it is currently off.
    """
    info = _az_json("search", "service", "show",
                    "--resource-group", rg, "--name", name)
    pid = ((info or {}).get("identity") or {}).get("principalId")  # type: ignore[union-attr]
    if pid:
        return pid
    log.info("Enabling system-assigned managed identity on search service %s/%s",
             rg, name)
    info = _az_json("search", "service", "update",
                    "--resource-group", rg, "--name", name,
                    "--identity-type", "SystemAssigned")
    pid = ((info or {}).get("identity") or {}).get("principalId")  # type: ignore[union-attr]
    if not pid:
        raise RuntimeError(
            f"Failed to enable system-assigned identity on {rg}/{name}; "
            f"got: {info}"
        )
    return pid


# ---------------------------------------------------------------------------
# Resource creation / detection
# ---------------------------------------------------------------------------

def ensure_storage_account(subscription_id: str, rg: str, location: str,
                           account_name: str, dry_run: bool) -> str:
    """Create or detect a StorageV2 account. Returns its resource id."""
    existing = _az("storage", "account", "show",
                   "--name", account_name, "--resource-group", rg,
                   check=False)
    if existing.returncode == 0:
        info = json.loads(existing.stdout)
        log.info("Reusing storage account %s/%s", rg, account_name)
        return info["id"]

    if dry_run:
        log.info("[dry-run] Would create storage account %s/%s in %s",
                 rg, account_name, location)
        return (f"/subscriptions/{subscription_id}/resourceGroups/{rg}"
                f"/providers/Microsoft.Storage/storageAccounts/{account_name}")

    log.info("Creating storage account %s/%s in %s", rg, account_name, location)
    info = _az_json(
        "storage", "account", "create",
        "--name", account_name,
        "--resource-group", rg,
        "--location", location,
        "--sku", "Standard_LRS",
        "--kind", "StorageV2",
        "--access-tier", "Hot",
        "--allow-blob-public-access", "false",
        "--min-tls-version", "TLS1_2",
    )
    return info["id"]  # type: ignore[index]


def ensure_container(account_name: str, container: str, dry_run: bool) -> None:
    """Create the blob container if it does not exist. Uses az login auth."""
    if dry_run:
        log.info("[dry-run] Would create container %s/%s", account_name, container)
        return
    existing = _az("storage", "container", "exists",
                   "--account-name", account_name,
                   "--name", container,
                   "--auth-mode", "login",
                   check=False)
    if existing.returncode == 0:
        try:
            data = json.loads(existing.stdout) if existing.stdout else {}
            if data.get("exists"):
                log.info("Reusing container %s/%s", account_name, container)
                return
        except json.JSONDecodeError:
            pass
    log.info("Creating container %s/%s", account_name, container)
    _az("storage", "container", "create",
        "--account-name", account_name,
        "--name", container,
        "--auth-mode", "login")


def ensure_role_assignment(principal_id: str, role_id: str,
                           scope: str, role_label: str, dry_run: bool) -> None:
    """Idempotent role assignment. role_id is a built-in role definition GUID."""
    if dry_run:
        log.info("[dry-run] Would assign %s to %s on %s",
                 role_label, principal_id, scope)
        return

    existing = _az("role", "assignment", "list",
                   "--assignee", principal_id,
                   "--role", role_id,
                   "--scope", scope,
                   check=False)
    if existing.returncode == 0 and existing.stdout.strip() not in ("", "[]"):
        log.info("Reusing role assignment: %s on %s", role_label, scope)
        return

    log.info("Assigning %s to %s on %s", role_label, principal_id, scope)
    _az("role", "assignment", "create",
        "--assignee-object-id", principal_id,
        "--assignee-principal-type", "ServicePrincipal" if len(principal_id) == 36 else "User",
        "--role", role_id,
        "--scope", scope)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def provision(dry_run: bool = False) -> dict:
    """Idempotently provision the storage account + container + RBAC.

    Returns a summary dict suitable for merging into provision.log.json.
    """
    sub = _az_json("account", "show", "--query", "id")
    subscription_id = sub  # type: ignore[assignment]
    if not isinstance(subscription_id, str):
        raise RuntimeError("Could not determine current az subscription. Run `az login`.")

    # Region + RG come from the search service.
    svc = _az_json("search", "service", "show",
                   "--resource-group", DEFAULT_SEARCH_RG,
                   "--name", DEFAULT_SEARCH_NAME)
    if not svc:
        raise RuntimeError(
            f"Search service {DEFAULT_SEARCH_RG}/{DEFAULT_SEARCH_NAME} not found. "
            f"Run back-end/infra/provision.py first or set "
            f"AZURE_SEARCH_RESOURCE_GROUP / AZURE_SEARCH_NAME env vars."
        )
    location = svc.get("location")  # type: ignore[union-attr]
    log.info("Provisioning pull-mode pipeline resources in %s/%s",
             DEFAULT_SEARCH_RG, location)

    storage_id = ensure_storage_account(
        subscription_id, DEFAULT_SEARCH_RG, location,
        DEFAULT_STORAGE_NAME, dry_run,
    )
    storage_account_name = storage_id.rsplit("/", 1)[-1]

    ensure_container(storage_account_name, DEFAULT_CONTAINER, dry_run)

    # Grant the search service its read access on the storage account.
    search_principal = _search_service_principal_id(DEFAULT_SEARCH_RG,
                                                    DEFAULT_SEARCH_NAME)
    ensure_role_assignment(
        principal_id=search_principal,
        role_id=STORAGE_BLOB_DATA_READER_ID,
        scope=storage_id,
        role_label="Storage Blob Data Reader",
        dry_run=dry_run,
    )

    # Grant the signed-in user upload access (Contributor).
    me = _current_signed_in_object_id()
    if me:
        ensure_role_assignment(
            principal_id=me,  # type: ignore[arg-type]
            role_id=STORAGE_BLOB_DATA_CONTRIBUTOR_ID,
            scope=storage_id,
            role_label="Storage Blob Data Contributor (you)",
            dry_run=dry_run,
        )

    summary = {
        "storage_account_resource_id": storage_id,
        "storage_account_name": storage_account_name,
        "storage_container": DEFAULT_CONTAINER,
        "search_service_principal_id": search_principal,
    }
    log.info("Pull-mode pipeline resources ready: %s",
             json.dumps(summary, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        provision(dry_run=args.dry_run)
    except subprocess.CalledProcessError as e:
        log.error("az command failed: %s\nstderr:\n%s", e, e.stderr or "")
        return 2
    except RuntimeError as e:
        log.error("%s", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
