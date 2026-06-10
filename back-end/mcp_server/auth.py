"""Entra bearer-token validation middleware (contract MCP-SERVER-ENTRA #90)."""

from __future__ import annotations

import logging
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import McpConfig

logger = logging.getLogger("mcp_server.auth")


@dataclass
class AuthContext:
    """Per-request authenticated principal + raw user assertion (for OBO)."""

    user_assertion: str
    claims: dict[str, Any]

    @property
    def oid(self) -> str:
        return str(self.claims.get("oid") or self.claims.get("sub") or "")

    @property
    def upn(self) -> str:
        return str(
            self.claims.get("upn")
            or self.claims.get("preferred_username")
            or self.claims.get("email")
            or ""
        )

    @property
    def scopes(self) -> list[str]:
        scp = self.claims.get("scp")
        if isinstance(scp, str):
            return scp.split()
        if isinstance(scp, list):
            return [str(s) for s in scp]
        return []


_current_auth: ContextVar[AuthContext | None] = ContextVar("_current_auth", default=None)


def get_current_auth() -> AuthContext:
    ctx = _current_auth.get()
    if ctx is None:
        raise RuntimeError(
            "No auth context bound to this request. The MCP tool was invoked "
            "outside the EntraAuthMiddleware — this is a programming error."
        )
    return ctx


def set_current_auth_for_tests(ctx: AuthContext | None) -> Any:
    """Test helper: bind an AuthContext outside the middleware. Returns a
    token suitable for ``_current_auth.reset(token)``."""
    return _current_auth.set(ctx)


class _AuthError(Exception):
    def __init__(self, code: str, description: str, status: int):
        super().__init__(description)
        self.code = code
        self.description = description
        self.status = status


class _JwksCache:
    def __init__(self, jwks_uri: str, refresh_cooldown_seconds: float = 30.0):
        self._uri = jwks_uri
        self._cooldown = refresh_cooldown_seconds
        self._client: PyJWKClient | None = None
        self._last_refresh = 0.0
        self._lock = threading.Lock()

    def _build(self) -> PyJWKClient:
        return PyJWKClient(self._uri, cache_keys=True, max_cached_keys=16)

    def get_signing_key(self, token: str):
        with self._lock:
            if self._client is None:
                self._client = self._build()
        try:
            return self._client.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientError:
            with self._lock:
                now = time.monotonic()
                if now - self._last_refresh < self._cooldown:
                    raise
                self._last_refresh = now
                self._client = self._build()
            return self._client.get_signing_key_from_jwt(token)


def _challenge(error: str, description: str, status: int) -> JSONResponse:
    headers = {
        "WWW-Authenticate": (
            f'Bearer error="{error}", error_description="{description}"'
        )
    }
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers=headers,
    )


class EntraAuthMiddleware:
    """ASGI middleware that enforces Entra ID bearer auth on ``protect_path``."""

    def __init__(
        self,
        app: ASGIApp,
        mcp_config: McpConfig,
        protect_path: str,
        anonymous_paths: tuple[str, ...] = ("/health",),
    ):
        self.app = app
        self.cfg = mcp_config
        self.protect_path = protect_path
        self.anonymous_paths = anonymous_paths
        self._jwks = _JwksCache(mcp_config.jwks_uri)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.anonymous_paths or not path.startswith(self.protect_path):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            response = _challenge(
                "invalid_token",
                "Missing or malformed Authorization header",
                status=401,
            )
            await response(scope, receive, send)
            return

        token = auth_header.split(None, 1)[1].strip()
        try:
            claims = self._validate_token(token)
        except _AuthError as e:
            response = _challenge(e.code, e.description, e.status)
            await response(scope, receive, send)
            return

        token_ctx = _current_auth.set(AuthContext(user_assertion=token, claims=claims))
        try:
            await self.app(scope, receive, send)
        finally:
            _current_auth.reset(token_ctx)

    def _validate_token(self, token: str) -> dict[str, Any]:
        cfg = self.cfg
        try:
            signing_key = self._jwks.get_signing_key(token)
        except jwt.PyJWKClientError as e:
            logger.warning("JWKS lookup failed: %s", e)
            raise _AuthError("invalid_token", "Unable to resolve signing key", 401)

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512"],
                audience=cfg.effective_audience,
                issuer=list(cfg.accepted_issuers),
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError:
            raise _AuthError("invalid_token", "Token expired", 401)
        except jwt.InvalidAudienceError:
            logger.warning("Token audience mismatch (expected=%s)", cfg.effective_audience)
            raise _AuthError("invalid_token", "Wrong audience", 401)
        except jwt.InvalidIssuerError:
            logger.warning("Token issuer mismatch (expected=%s)", cfg.issuer)
            raise _AuthError("invalid_token", "Wrong issuer", 401)
        except jwt.InvalidTokenError as e:
            raise _AuthError("invalid_token", f"Invalid token: {e}", 401)

        scopes = claims.get("scp")
        if isinstance(scopes, str):
            scope_set = set(scopes.split())
        elif isinstance(scopes, list):
            scope_set = {str(s) for s in scopes}
        else:
            scope_set = set()
        if cfg.required_scope not in scope_set:
            raise _AuthError(
                "insufficient_scope",
                f"Required scope '{cfg.required_scope}' not present",
                403,
            )
        return claims
