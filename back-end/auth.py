"""Entra ID (Azure AD) OAuth 2.0 bearer token validation middleware.

Validates JWT access tokens issued by Microsoft Entra ID. Used to protect
the /api/* skill endpoints consumed by Copilot Studio.

Required env vars:
  AZURE_AD_TENANT_ID  — Your Entra tenant ID
  AZURE_AD_CLIENT_ID  — The app registration (audience) for this API
"""

from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any

import jwt
import requests
from flask import jsonify, request

log = logging.getLogger(__name__)

_JWKS_CACHE: dict[str, Any] = {}


def _get_tenant_id() -> str:
    return os.getenv("AZURE_AD_TENANT_ID", os.getenv("AZURE_TENANT_ID", "")).strip()


def _get_client_id() -> str:
    return os.getenv("AZURE_AD_CLIENT_ID", "").strip()


def _get_jwks_client(tenant_id: str) -> jwt.PyJWKClient:
    """Cached JWKS client for the tenant's OpenID configuration."""
    if tenant_id not in _JWKS_CACHE:
        oidc_url = (
            f"https://login.microsoftonline.com/{tenant_id}/v2.0/"
            f".well-known/openid-configuration"
        )
        oidc = requests.get(oidc_url, timeout=10).json()
        jwks_uri = oidc["jwks_uri"]
        _JWKS_CACHE[tenant_id] = jwt.PyJWKClient(jwks_uri)
    return _JWKS_CACHE[tenant_id]


def _validate_token(token: str) -> dict[str, Any]:
    """Decode and validate a bearer token. Returns the claims dict."""
    tenant_id = _get_tenant_id()
    client_id = _get_client_id()

    if not tenant_id or not client_id:
        raise ValueError(
            "AZURE_AD_TENANT_ID and AZURE_AD_CLIENT_ID must be set for OAuth."
        )

    jwks_client = _get_jwks_client(tenant_id)
    signing_key = jwks_client.get_signing_key_from_jwt(token)

    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        options={"require": ["exp", "iss", "aud"]},
    )
    return claims


def require_auth(f):
    """Decorator that enforces Entra ID bearer token on a route.

    If AZURE_AD_CLIENT_ID is not set, auth is bypassed (dev mode).
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        # Dev mode: skip auth if not configured
        if not _get_client_id():
            log.debug("Auth skipped — AZURE_AD_CLIENT_ID not configured")
            return f(*args, **kwargs)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "unauthorized",
                            "message": "Missing or invalid Authorization header"}), 401

        token = auth_header[7:]
        try:
            claims = _validate_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "unauthorized",
                            "message": "Token has expired"}), 401
        except jwt.InvalidTokenError as e:
            log.warning("Token validation failed: %s", e)
            return jsonify({"error": "unauthorized",
                            "message": "Invalid token"}), 401
        except Exception as e:  # noqa: BLE001
            log.error("Auth error: %s", e)
            return jsonify({"error": "unauthorized",
                            "message": "Authentication failed"}), 401

        # Attach claims to request context for downstream use
        request.environ["auth_claims"] = claims
        return f(*args, **kwargs)

    return decorated
