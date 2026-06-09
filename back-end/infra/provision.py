"""Idempotent provisioning for ARB Bot Foundry v2 dependencies.

Ensures a Canada Central AI Services account hosts a gpt-5.4-pro deployment
with code interpreter, plus a Foundry v2 project and (optionally) a text
embedding deployment used by the Azure AI Search index builder.

Reuses existing resources where possible. Writes resource IDs to
``back-end/.env.example`` (no secrets). Exits non-zero on any blocker.

Usage:
    python back-end/infra/provision.py
    python back-end/infra/provision.py --dry-run
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

# Load environment variables from a .env file at the repository root (one
# level above back-end/) before reading any os.getenv() calls below. Existing
# process env vars take precedence (override=False) so callers can still
# override .env values inline (e.g. `$env:FOUNDRY_MODEL = "..."`).
try:
    from dotenv import load_dotenv

    _REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
    if _REPO_ROOT_ENV.is_file():
        load_dotenv(_REPO_ROOT_ENV, override=False)
except ImportError:
    pass

# Resolve the az CLI executable once. On Windows the launcher is az.cmd; using
# subprocess.run([...], shell=False) with a bare "az" raises FileNotFoundError
# (WinError 2) because CreateProcess does not honour PATHEXT. shutil.which()
# does, returning the full path with the correct extension.
AZ_EXE = shutil.which("az") or "az"

LOCATION = os.getenv("FOUNDRY_LOCATION", "canadacentral")
MODEL_NAME = os.getenv("FOUNDRY_MODEL", "gpt-5.4-pro")
EMBEDDING_MODEL_NAME = os.getenv("FOUNDRY_EMBEDDING_MODEL", "text-embedding-3-large")
DEFAULT_PROJECT_NAME = os.getenv("FOUNDRY_PROJECT_NAME", "arb")
DEFAULT_RG_NAME = os.getenv("FOUNDRY_RESOURCE_GROUP", "rg-arb-foundry-cc")
DEFAULT_ACCOUNT_PREFIX = os.getenv("FOUNDRY_ACCOUNT_PREFIX", "arb-foundry-cc")

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / "back-end" / ".env.example"
LOG_FILE = REPO_ROOT / "back-end" / "infra" / "provision.log.json"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("provision")


class ProvisionError(RuntimeError):
    """Provisioning blocker; printed verbatim and exits non-zero."""


def az(*args: str, check: bool = True, capture: bool = True) -> Any:
    """Run an `az` command, return parsed JSON on success."""
    cmd = [AZ_EXE, *args, "-o", "json"]
    log.debug("$ %s", " ".join(cmd))
    res = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        shell=False,
    )
    if check and res.returncode != 0:
        raise ProvisionError(f"az failed: {' '.join(cmd)}\n{res.stderr.strip()}")
    if not res.stdout.strip():
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return res.stdout


def require_login() -> dict[str, str]:
    try:
        acct = az("account", "show")
    except ProvisionError as e:
        raise ProvisionError(
            "az not logged in or no default subscription. "
            "Remediation: `az login` then `az account set -s <sub>`."
        ) from e
    return {"subscription_id": acct["id"], "tenant_id": acct["tenantId"]}


def find_existing_account_with_model() -> dict[str, str] | None:
    accounts = az("cognitiveservices", "account", "list") or []
    candidates = [
        a for a in accounts
        if (a.get("location") or "").lower() == LOCATION
        and (a.get("kind") or "") == "AIServices"
    ]
    log.info("Found %d AIServices account(s) in %s", len(candidates), LOCATION)
    for a in candidates:
        rg = a["resourceGroup"]
        name = a["name"]
        try:
            deps = az(
                "cognitiveservices", "account", "deployment", "list",
                "-g", rg, "-n", name,
            ) or []
        except ProvisionError as e:
            log.warning("Skipping %s/%s (cannot list deployments): %s", rg, name, e)
            continue
        for d in deps:
            model = (d.get("properties", {}).get("model") or {}).get("name", "")
            if model == MODEL_NAME:
                log.info(
                    "Reusing existing account=%s/%s with deployment=%s",
                    rg, name, d["name"],
                )
                return {
                    "resource_group": rg,
                    "account_name": name,
                    "endpoint": a.get("properties", {}).get("endpoint", ""),
                    "deployment_name": d["name"],
                }
    return None


def check_model_availability() -> None:
    try:
        models = az("cognitiveservices", "model", "list", "-l", LOCATION) or []
    except ProvisionError as e:
        raise ProvisionError(f"Cannot list models in {LOCATION}: {e}") from e
    available = [
        m for m in models
        if (m.get("model") or {}).get("name") == MODEL_NAME
    ]
    if not available:
        # Surface neighbouring options for the operator.
        gpt_models = sorted({
            (m.get("model") or {}).get("name", "")
            for m in models
            if (m.get("model") or {}).get("name", "").startswith("gpt-")
        })
        raise ProvisionError(
            f"Model '{MODEL_NAME}' is NOT available in {LOCATION}.\n"
            f"Available gpt-* models: {', '.join(gpt_models) or 'none'}\n"
            "Remediation: set FOUNDRY_MODEL env var to an available model, "
            "or change region via FOUNDRY_LOCATION, or open a quota/availability "
            "request with Azure support."
        )


def ensure_resource_group(sub_id: str) -> str:
    rgs = az("group", "list") or []
    if any(r["name"] == DEFAULT_RG_NAME for r in rgs):
        log.info("Reusing resource group %s", DEFAULT_RG_NAME)
    else:
        log.info("Creating resource group %s in %s", DEFAULT_RG_NAME, LOCATION)
        az("group", "create", "-n", DEFAULT_RG_NAME, "-l", LOCATION)
    return DEFAULT_RG_NAME


def ensure_account(rg: str) -> dict[str, str]:
    accounts = az("cognitiveservices", "account", "list", "-g", rg) or []
    aiservices = [
        a for a in accounts
        if (a.get("kind") or "") == "AIServices"
        and (a.get("location") or "").lower() == LOCATION
    ]
    if aiservices:
        a = sorted(aiservices, key=lambda x: x["name"])[0]
        log.info("Reusing account %s", a["name"])
        return {
            "name": a["name"],
            "endpoint": a.get("properties", {}).get("endpoint", ""),
        }
    name = f"{DEFAULT_ACCOUNT_PREFIX}-{os.urandom(2).hex()}"
    log.info("Creating AIServices account %s", name)
    a = az(
        "cognitiveservices", "account", "create",
        "-g", rg, "-n", name,
        "--kind", "AIServices",
        "--sku", "S0",
        "-l", LOCATION,
        "--yes",
        "--assign-identity",
    )
    return {"name": name, "endpoint": a.get("properties", {}).get("endpoint", "")}


def _get_latest_model_version(model_name: str) -> str:
    """Look up the latest OpenAI-format version for ``model_name`` in LOCATION."""
    models = az("cognitiveservices", "model", "list", "-l", LOCATION) or []
    versions: list[str] = []
    for m in models:
        info = m.get("model") or {}
        if info.get("name") == model_name and (info.get("format") or "OpenAI") == "OpenAI":
            ver = info.get("version")
            if ver:
                versions.append(ver)
    if not versions:
        raise ProvisionError(
            f"No OpenAI versions discoverable for model '{model_name}' in {LOCATION}. "
            "Run `az cognitiveservices model list -l <location>` to inspect."
        )
    # Versions are date-stamped strings (e.g. '2024-08-06') that sort correctly lexicographically.
    return sorted(versions)[-1]


def ensure_managed_identity(rg: str, account: str) -> None:
    """Ensure the AI Services account has a System Assigned Managed Identity.

    Foundry v2 project creation requires this; reused accounts created before
    this script may not have it enabled.
    """
    info = az("cognitiveservices", "account", "show", "-g", rg, "-n", account) or {}
    identity_type = ((info.get("identity") or {}).get("type") or "").lower()
    if "systemassigned" in identity_type:
        log.debug("Managed identity already enabled on %s", account)
        return
    log.info("Enabling System Assigned Managed Identity on %s", account)
    az("cognitiveservices", "account", "identity", "assign",
       "-g", rg, "-n", account, "--mi-system-assigned")


def ensure_deployment(rg: str, account: str, model_name: str,
                       deployment_name: str | None = None,
                       capability: dict[str, str] | None = None) -> str:
    deployment_name = deployment_name or model_name
    deps = az("cognitiveservices", "account", "deployment", "list",
              "-g", rg, "-n", account) or []
    existing = next((d for d in deps
                     if (d.get("properties", {}).get("model") or {}).get("name") == model_name),
                    None)
    if existing:
        log.info("Reusing deployment %s (model %s)", existing["name"], model_name)
        return existing["name"]
    model_version = _get_latest_model_version(model_name)
    # Try newer GlobalStandard SKU first (preferred for AI workloads, broader
    # region availability), fall back to Standard. Surface the last error if both
    # fail so the caller can react.
    last_err: ProvisionError | None = None
    for sku_name in ("GlobalStandard", "Standard"):
        log.info("Creating deployment %s (model %s, version %s, sku %s)",
                 deployment_name, model_name, model_version, sku_name)
        args = [
            "cognitiveservices", "account", "deployment", "create",
            "-g", rg, "-n", account,
            "--deployment-name", deployment_name,
            "--model-name", model_name,
            "--model-version", model_version,
            "--model-format", "OpenAI",
            "--sku-capacity", "1",
            "--sku-name", sku_name,
        ]
        if capability:
            for k, v in capability.items():
                args.extend(["--capability", f"{k}={v}"])
        try:
            az(*args)
            return deployment_name
        except ProvisionError as e:
            last_err = e
            log.warning("SKU %s rejected for %s: trying fallback", sku_name, model_name)
    raise last_err or ProvisionError(f"Could not create deployment for {model_name}")


def ensure_project(rg: str, account: str, project_name: str) -> dict[str, str]:
    """Ensure a Foundry v2 project exists under the AI Services account.

    Uses ``az cognitiveservices account project`` (available in az CLI >= 2.86).
    Reuses an existing project named ``project_name`` if present; otherwise
    reuses the default project the AI Services resource auto-created; otherwise
    creates a new one. The first-class subcommand is preferred over ``az rest``
    because it handles identity assignment and api-version selection internally.
    """
    projects = az("cognitiveservices", "account", "project", "list",
                  "-g", rg, "-n", account) or []

    def _extract_endpoint(p: dict[str, Any]) -> str:
        endpoints = (p.get("properties") or {}).get("endpoints") or {}
        # Prefer the AI Foundry API endpoint; fall back to the first available.
        return endpoints.get("AI Foundry API") or next(iter(endpoints.values()), "")

    # Project objects are returned with the composite name "<account>/<project>";
    # the short name we created with lives at the tail.
    def _short_name(p: dict[str, Any]) -> str:
        full = p.get("name", "") or ""
        return full.split("/", 1)[-1] if "/" in full else full

    named = next((p for p in projects if _short_name(p) == project_name), None)
    if named:
        log.info("Reusing project %s", project_name)
        return {"name": project_name, "endpoint": _extract_endpoint(named)}

    default_proj = next((p for p in projects
                          if (p.get("properties") or {}).get("isDefault") is True),
                         None)
    if default_proj:
        short = _short_name(default_proj)
        log.info("Reusing default project %s", short)
        return {"name": short, "endpoint": _extract_endpoint(default_proj)}

    log.info("Creating Foundry v2 project %s", project_name)
    created = az(
        "cognitiveservices", "account", "project", "create",
        "-g", rg, "-n", account,
        "--project-name", project_name,
        "-l", LOCATION,
        "--assign-identity",
    )
    return {
        "name": project_name,
        "endpoint": _extract_endpoint(created or {}),
    }


def write_env_example(values: dict[str, str]) -> None:
    lines = [
        "# Generated by back-end/infra/provision.py — non-secret resource IDs only.",
        "# Copy to .env.local and load via DefaultAzureCredential at runtime.",
        "",
    ]
    for k, v in values.items():
        lines.append(f"{k}={v}")
    ENV_EXAMPLE.parent.mkdir(parents=True, exist_ok=True)
    ENV_EXAMPLE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("Wrote %s", ENV_EXAMPLE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        ids = require_login()
        log.info("Subscription=%s Tenant=%s Location=%s Model=%s",
                 ids["subscription_id"], ids["tenant_id"], LOCATION, MODEL_NAME)

        existing = find_existing_account_with_model()
        if existing:
            rg = existing["resource_group"]
            account_name = existing["account_name"]
            endpoint = existing["endpoint"]
            deployment = existing["deployment_name"]
        else:
            check_model_availability()
            if args.dry_run:
                log.info("[dry-run] Would create RG=%s account=<%s-xxxx> deployment=%s",
                         DEFAULT_RG_NAME, DEFAULT_ACCOUNT_PREFIX, MODEL_NAME)
                return 0
            rg = ensure_resource_group(ids["subscription_id"])
            account = ensure_account(rg)
            account_name = account["name"]
            endpoint = account["endpoint"]
            deployment = ensure_deployment(
                rg, account_name, MODEL_NAME,
                capability={"codeInterpreter": "true"},
            )

        # Embeddings deployment for SEARCH-REFACTOR
        try:
            ensure_deployment(rg, account_name, EMBEDDING_MODEL_NAME)
            embedding_dep = EMBEDDING_MODEL_NAME
        except ProvisionError as e:
            log.warning("Skipping embeddings deployment: %s", e)
            embedding_dep = ""

        if args.dry_run:
            project_info = {"name": DEFAULT_PROJECT_NAME, "endpoint": "<dry-run>"}
        else:
            ensure_managed_identity(rg, account_name)
            project_info = ensure_project(rg, account_name, DEFAULT_PROJECT_NAME)

        values = {
            "AZURE_SUBSCRIPTION_ID": ids["subscription_id"],
            "AZURE_TENANT_ID": ids["tenant_id"],
            "FOUNDRY_RESOURCE_GROUP": rg,
            "FOUNDRY_ACCOUNT_NAME": account_name,
            "FOUNDRY_ENDPOINT": endpoint,
            "FOUNDRY_PROJECT_NAME": project_info["name"],
            "FOUNDRY_PROJECT_ENDPOINT": project_info.get("endpoint", ""),
            "FOUNDRY_MODEL_DEPLOYMENT": deployment,
            "FOUNDRY_EMBEDDINGS_DEPLOYMENT": embedding_dep,
            "AZURE_SEARCH_ENDPOINT": os.getenv("AZURE_SEARCH_ENDPOINT", ""),
        }

        # Pull-mode pipeline support (#61). Provisions the storage account +
        # container + RBAC needed by the arb-policies-blob data source.
        # Failures here do not block the rest of provisioning — they emit a
        # warning so users can re-run ``python -m infra.provision_search_pipeline``
        # independently if needed (e.g. they want to set ARB_STORAGE_ACCOUNT_NAME
        # first to use a friendlier name).
        if not args.dry_run and values["AZURE_SEARCH_ENDPOINT"]:
            try:
                from infra.provision_search_pipeline import provision as provision_pipeline
                pipeline = provision_pipeline(dry_run=False)
                values["STORAGE_ACCOUNT_RESOURCE_ID"] = pipeline["storage_account_resource_id"]
                values["STORAGE_CONTAINER"] = pipeline["storage_container"]
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "Pull-mode pipeline provisioning skipped (%s). Re-run "
                    "`python -m infra.provision_search_pipeline` after fixing.",
                    e,
                )
        elif not values["AZURE_SEARCH_ENDPOINT"]:
            log.warning(
                "AZURE_SEARCH_ENDPOINT not set — skipping pull-mode pipeline "
                "provisioning. Set it and re-run `python -m infra.provision_search_pipeline`."
            )

        write_env_example(values)
        LOG_FILE.write_text(json.dumps(values, indent=2), encoding="utf-8")
        log.info("Done. Summary written to %s", LOG_FILE)
        return 0

    except ProvisionError as e:
        log.error("BLOCKER: %s", e)
        return 2
    except KeyboardInterrupt:
        log.error("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
