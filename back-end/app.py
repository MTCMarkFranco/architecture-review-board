"""Flask entry-point for the ARB Bot back-end."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from flask_cors import CORS

from agents.config import Config, ConfigError
from agents.errors import (
    AgentNotFoundError,
    WorkflowError,
    WorkflowTimeoutError,
)
from agents.orchestrator import ArbWorkflow
from agents import auth
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
CORS(app)

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


def _authenticate(req) -> str | None:
    """Validate the incoming Entra bearer token and return the raw assertion.

    Returns ``None`` (auth disabled / pass-through) only when OBO is not
    configured, preserving the local CLI-credential dev flow. When OBO is
    configured a missing/invalid token raises ``AuthError``/``ScopeError``.
    """
    if not auth.obo_enabled():
        return None
    header = req.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise auth.AuthError("missing bearer token")
    token = header.split(" ", 1)[1].strip()
    auth.validate_bearer_token(token)  # raises on failure
    return token


@app.errorhandler(auth.AuthError)
def _handle_auth(e):
    resp = jsonify({"error": "unauthorized", "error_code": "unauthorized",
                    "message": str(e)})
    resp.status_code = 401
    resp.headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return resp


@app.errorhandler(auth.ScopeError)
def _handle_scope(e):
    resp = jsonify({"error": "forbidden", "error_code": "insufficient_scope",
                    "message": str(e)})
    resp.status_code = 403
    resp.headers["WWW-Authenticate"] = 'Bearer error="insufficient_scope"'
    return resp


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
    assertion = _authenticate(request)
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
    auth.set_current_assertion(assertion)
    try:
        findings = _run(_get_workflow().validate_bytes(file_bytes, f.filename))
    finally:
        auth.clear_current_assertion()
    return jsonify(findings)


@app.route("/geniac", methods=["POST"])
def geniac():
    assertion = _authenticate(request)
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
    auth.set_current_assertion(assertion)
    try:
        scripts = _run(_get_workflow().iac_bytes(file_bytes, f.filename))
    finally:
        auth.clear_current_assertion()
    return jsonify(scripts)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True)
