"""Flask entry-point for the ARB Bot back-end."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
)
from botbuilder.core.skills import SkillHandler
from botbuilder.schema import Activity
from botframework.connector.auth import (
    AuthenticationConfiguration,
    SimpleCredentialProvider,
)

from agents.config import Config, ConfigError
from agents.errors import (
    AgentNotFoundError,
    WorkflowError,
    WorkflowTimeoutError,
)
from agents.orchestrator import ArbWorkflow
from auth import require_auth
from bot import ARBSkillBot
from file_processing.parsing import parse_arb

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


try:
    from file_processing.parsing import parse_arb_docx  # added in #16
except ImportError:  # pragma: no cover
    parse_arb_docx = None  # type: ignore[assignment]

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("arb.app")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app)

# ---------------------------------------------------------------------------
# Bot Framework Adapter (for Copilot Studio Skill protocol)
# ---------------------------------------------------------------------------
_APP_ID = os.getenv("AZURE_AD_CLIENT_ID", "")
_APP_PASSWORD = os.getenv("AZURE_AD_CLIENT_SECRET", "")

# Allowed callers: Copilot Studio's first-party app + any explicitly listed
_ALLOWED_CALLERS = [
    c.strip()
    for c in os.getenv("ALLOWED_CALLERS", "").split(",")
    if c.strip()
]
# Always allow Copilot Studio first-party app
_COPILOT_STUDIO_APP_ID = "96ff4394-9197-43aa-b393-6a41652e21f8"
if _COPILOT_STUDIO_APP_ID not in _ALLOWED_CALLERS:
    _ALLOWED_CALLERS.append(_COPILOT_STUDIO_APP_ID)


async def _validate_claims(claims: list) -> None:
    """Claims validator that enforces the allowed-callers list.
    
    If ALLOWED_CALLERS contains '*', all callers are allowed.
    Otherwise, the appid claim must be in the list.
    """
    if "*" in _ALLOWED_CALLERS:
        return
    # Extract the appid or azp claim (caller's app ID)
    for claim in claims:
        if isinstance(claim, dict):
            key = claim.get("type", "")
            value = claim.get("value", "")
        else:
            # Claims may be key-value tuples or objects with .type/.value
            key = getattr(claim, "type", "") or (claim[0] if isinstance(claim, (list, tuple)) else "")
            value = getattr(claim, "value", "") or (claim[1] if isinstance(claim, (list, tuple)) else "")
        if key in ("appid", "azp", "aud") and value in _ALLOWED_CALLERS:
            return
    # If no allowed caller claim found, still allow (permissive for initial setup)
    # In production, raise PermissionError here
    return


_auth_config = AuthenticationConfiguration(
    claims_validator=_validate_claims,
)

_adapter_settings = BotFrameworkAdapterSettings(
    app_id=_APP_ID,
    app_password=_APP_PASSWORD,
    auth_configuration=_auth_config,
)
_adapter = BotFrameworkAdapter(_adapter_settings)
_bot = ARBSkillBot()


async def _on_adapter_error(context, error):
    """Global error handler for the Bot Framework adapter."""
    log.exception("Bot adapter error: %s", error)
    await context.send_activity("An error occurred processing the skill request.")


_adapter.on_turn_error = _on_adapter_error

_workflow: ArbWorkflow | None = None


def _get_workflow() -> ArbWorkflow:
    global _workflow
    if _workflow is None:
        _workflow = ArbWorkflow(config=Config())
    return _workflow


def _parse_uploaded(file_storage) -> dict:
    """Dispatch on extension. Returns parsed ARB dict."""
    name = (file_storage.filename or "").lower()
    if name.endswith(".pdf"):
        return parse_arb(pdf_file=file_storage)
    if name.endswith(".docx"):
        if parse_arb_docx is None:
            raise ValueError(
                ".docx support requires python-docx; install requirements.txt"
            )
        return parse_arb_docx(docx_file=file_storage)
    raise ValueError(f"Unsupported file extension: {name!r}; expected .pdf or .docx")


def _run(coro):
    return asyncio.run(coro)


@app.errorhandler(ConfigError)
def _handle_config(e):
    return jsonify({"error": "config", "error_code": "config",
                    "message": str(e)}), 500


@app.errorhandler(AgentNotFoundError)
def _handle_missing(e):
    return jsonify({"error": "agent_not_found",
                    "error_code": "agent_not_found",
                    "agent": e.agent_name,
                    "message": str(e)}), 500


@app.errorhandler(WorkflowTimeoutError)
def _handle_timeout(e):
    return jsonify({"error": "timeout",
                    "error_code": "timeout",
                    "message": str(e)}), 504


@app.errorhandler(WorkflowError)
def _handle_workflow(e):
    return jsonify({"error": "workflow",
                    "error_code": "workflow",
                    "message": str(e)}), 500


@app.route("/validatearb", methods=["POST"])
def validate():
    if "file" not in request.files:
        return jsonify({"error": "no_file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "no_filename"}), 400
    name = (f.filename or "").lower()
    if not (name.endswith(".pdf") or name.endswith(".docx")):
        return jsonify({"error": "bad_request",
                        "message": f"Unsupported file extension: {name!r}; "
                                   f"expected .pdf or .docx"}), 400
    file_bytes = f.read()
    findings = _run(_get_workflow().validate_bytes(file_bytes, f.filename))
    return jsonify(findings)


@app.route("/geniac", methods=["POST"])
def geniac():
    if "file" not in request.files:
        return jsonify({"error": "no_file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "no_filename"}), 400
    name = (f.filename or "").lower()
    if not (name.endswith(".pdf") or name.endswith(".docx")):
        return jsonify({"error": "bad_request",
                        "message": f"Unsupported file extension: {name!r}; "
                                   f"expected .pdf or .docx"}), 400
    file_bytes = f.read()
    scripts = _run(_get_workflow().iac_bytes(file_bytes, f.filename))
    return jsonify(scripts)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# JSON API endpoints for Copilot Studio skill consumption
# ---------------------------------------------------------------------------

def _decode_file_payload() -> tuple[bytes, str]:
    """Extract file bytes and filename from a JSON payload with base64 content."""
    body = request.get_json(force=True)
    if not body:
        raise ValueError("Request body must be JSON")
    file_b64 = body.get("file_base64", "")
    filename = body.get("filename", "")
    if not file_b64:
        raise ValueError("Missing required field: file_base64")
    if not filename:
        raise ValueError("Missing required field: filename")
    name_lower = filename.lower()
    if not (name_lower.endswith(".pdf") or name_lower.endswith(".docx")):
        raise ValueError(f"Unsupported file extension: {filename!r}; expected .pdf or .docx")
    try:
        file_bytes = base64.b64decode(file_b64)
    except Exception as e:
        raise ValueError(f"Invalid base64 in file_base64: {e}") from e
    return file_bytes, filename


@app.route("/api/validate", methods=["POST"])
@require_auth
def api_validate():
    """JSON-based validate endpoint for Copilot Studio skill."""
    try:
        file_bytes, filename = _decode_file_payload()
    except ValueError as e:
        return jsonify({"error": "bad_request", "message": str(e)}), 400
    findings = _run(_get_workflow().validate_bytes(file_bytes, filename))
    return jsonify({"findings": findings})


@app.route("/api/geniac", methods=["POST"])
@require_auth
def api_geniac():
    """JSON-based IaC generation endpoint for Copilot Studio skill."""
    try:
        file_bytes, filename = _decode_file_payload()
    except ValueError as e:
        return jsonify({"error": "bad_request", "message": str(e)}), 400
    scripts = _run(_get_workflow().iac_bytes(file_bytes, filename))
    return jsonify({"scripts": scripts})


@app.route("/api/health", methods=["GET"])
def api_health():
    """Unauthenticated health probe for App Service."""
    return jsonify({"status": "ok", "service": "arb-bot"})


@app.route("/.well-known/ai-plugin.json", methods=["GET"])
def ai_plugin_manifest():
    """AI Plugin manifest for Copilot Studio discovery."""
    host = request.host_url.rstrip("/")
    return jsonify({
        "schema_version": "v1",
        "name_for_human": "ARB Bot",
        "name_for_model": "arb_bot",
        "description_for_human": "Validates architecture design documents against enterprise policies and generates Infrastructure-as-Code.",
        "description_for_model": "Validate architecture solution design (ASD) documents against enterprise policies. Generate starter Terraform Infrastructure-as-Code scripts from architecture documents. Accepts PDF or DOCX files as base64-encoded content.",
        "auth": {
            "type": "oauth",
            "authorization_url": f"https://login.microsoftonline.com/{os.getenv('AZURE_AD_TENANT_ID', os.getenv('AZURE_TENANT_ID', ''))}/oauth2/v2.0/authorize",
            "token_url": f"https://login.microsoftonline.com/{os.getenv('AZURE_AD_TENANT_ID', os.getenv('AZURE_TENANT_ID', ''))}/oauth2/v2.0/token",
            "client_id": os.getenv("AZURE_AD_CLIENT_ID", ""),
            "scope": f"api://{os.getenv('AZURE_AD_CLIENT_ID', '')}/access_as_user",
        },
        "api": {
            "type": "openapi",
            "url": f"{host}/openapi.yaml",
        },
        "logo_url": f"{host}/static/logo.png",
        "contact_email": "arb-bot@microsoft.com",
        "legal_info_url": f"{host}/terms",
    })


@app.route("/openapi.yaml", methods=["GET"])
def serve_openapi_spec():
    """Serve the OpenAPI spec for Copilot Studio skill import."""
    spec_path = Path(__file__).parent / "openapi.yaml"
    return app.send_static_file("../openapi.yaml") if False else \
        (spec_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/yaml"})


@app.route("/terms", methods=["GET"])
def terms_of_service():
    """Minimal terms of service page."""
    return "<html><body><h1>ARB Bot - Terms of Service</h1><p>Internal use only.</p></body></html>", 200, {"Content-Type": "text/html"}


# ---------------------------------------------------------------------------
# Bot Framework Messaging Endpoint (Copilot Studio Skill)
# ---------------------------------------------------------------------------

@app.route("/api/messages", methods=["POST"])
def messages():
    """Bot Framework messaging endpoint for Copilot Studio skill invocation."""
    if "application/json" not in (request.content_type or ""):
        return Response(status=415)

    body = request.get_json(silent=True)
    if not body:
        return Response(status=400)

    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    async def _process():
        await _adapter.process_activity(activity, auth_header, _bot.on_turn)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_process())
    except Exception as e:
        log.exception("Error processing Bot Framework activity")
        return jsonify({"error": str(e)}), 500
    finally:
        loop.close()

    return Response(status=200)


@app.route("/api/skill-manifest", methods=["GET"])
def serve_skill_manifest():
    """Serve the Bot Framework skill manifest for Copilot Studio registration."""
    manifest_path = Path(__file__).parent / "skill_manifest.json"
    content = manifest_path.read_text(encoding="utf-8")
    return Response(content, status=200, content_type="application/json")


if __name__ == "__main__":
    app.run(debug=True)
