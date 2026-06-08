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
import subprocess
import sys
from pathlib import Path
from typing import Any

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
    cmd = ["az", *args, "-o", "json"]
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
    log.info("Creating deployment %s (model %s)", deployment_name, model_name)
    args = [
        "cognitiveservices", "account", "deployment", "create",
        "-g", rg, "-n", account,
        "--deployment-name", deployment_name,
        "--model-name", model_name,
        "--model-format", "OpenAI",
        "--sku-capacity", "1",
        "--sku-name", "Standard",
    ]
    if capability:
        for k, v in capability.items():
            args.extend(["--capability", f"{k}={v}"])
    az(*args)
    return deployment_name


def ensure_project(rg: str, account: str, project_name: str) -> dict[str, str]:
    """Create a Foundry v2 project under the AI Services account.

    Uses the resource manager REST surface via `az rest` because not every
    `az cognitiveservices` build exposes a first-class `project create`
    subcommand.
    """
    sub_id = az("account", "show")["id"]
    base = (
        f"/subscriptions/{sub_id}/resourceGroups/{rg}/providers/"
        f"Microsoft.CognitiveServices/accounts/{account}/projects/{project_name}"
    )
    api_version = "2025-04-01-preview"
    try:
        existing = az("rest", "--method", "get",
                      "--url", f"https://management.azure.com{base}?api-version={api_version}")
        if existing and existing.get("name") == project_name:
            log.info("Reusing project %s", project_name)
            return {
                "name": project_name,
                "endpoint": existing.get("properties", {}).get("endpoint", ""),
            }
    except ProvisionError:
        pass

    log.info("Creating Foundry v2 project %s", project_name)
    body = json.dumps({"location": LOCATION, "properties": {}})
    created = az("rest", "--method", "put",
                 "--url", f"https://management.azure.com{base}?api-version={api_version}",
                 "--body", body,
                 "--headers", "Content-Type=application/json")
    return {
        "name": project_name,
        "endpoint": (created or {}).get("properties", {}).get("endpoint", ""),
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
