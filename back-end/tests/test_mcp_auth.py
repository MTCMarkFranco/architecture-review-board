"""Unit tests for mcp_server.auth (token validation matrix).

These tests construct local RSA-signed JWTs and a stub JWKS so the
middleware exercises real signature/issuer/audience/scope/expiry paths
without touching Entra.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

from mcp_server.auth import EntraAuthMiddleware, _JwksCache, get_current_auth
from mcp_server.config import McpConfig


# ---------------------------------------------------------------------------
# RSA key + JWKS fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend(),
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()

    def _b64(n: int) -> str:
        import base64
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    jwk = {
        "kty": "RSA",
        "kid": "test-kid",
        "use": "sig",
        "alg": "RS256",
        "n": _b64(public_numbers.n),
        "e": _b64(public_numbers.e),
    }
    return private_pem, jwk


@pytest.fixture()
def cfg() -> McpConfig:
    c = McpConfig()
    c.tenant_id = "11111111-1111-1111-1111-111111111111"
    c.api_client_id = "22222222-2222-2222-2222-222222222222"
    c.api_audience = f"api://{c.api_client_id}"
    c.required_scope = "ARB.Invoke"
    c.issuer_override = f"https://login.microsoftonline.com/{c.tenant_id}/v2.0"
    return c


@pytest.fixture()
def stub_middleware(rsa_keypair, cfg, monkeypatch):
    _, jwk = rsa_keypair

    class _StubJwksCache(_JwksCache):
        def get_signing_key(self, token: str):
            class _K:
                def __init__(self, jwk_dict):
                    self._jwk = jwk_dict

                @property
                def key(self):
                    # Convert JWK back to a public key object PyJWT can use.
                    from jwt.algorithms import RSAAlgorithm
                    return RSAAlgorithm.from_jwk(json.dumps(self._jwk))
            return _K(jwk)

    async def downstream(scope, receive, send):
        # Echo the auth context so tests can assert it.
        ctx = get_current_auth()
        body = json.dumps({"oid": ctx.oid, "scopes": ctx.scopes}).encode("utf-8")
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})

    mw = EntraAuthMiddleware(
        downstream, mcp_config=cfg, protect_path="/api/mcp",
    )
    mw._jwks = _StubJwksCache("https://example/jwks")
    return mw


def _mint_token(rsa_keypair, **overrides) -> str:
    private_pem, _ = rsa_keypair
    now = int(time.time())
    claims = {
        "iss": "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/v2.0",
        "aud": "api://22222222-2222-2222-2222-222222222222",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "scp": "ARB.Invoke",
        "oid": "user-oid-1",
        "preferred_username": "alice@example.com",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="RS256",
                      headers={"kid": "test-kid"})


def _invoke(mw, *, token: str | None = None, path: str = "/api/mcp") -> tuple[int, dict[str, Any], dict[str, Any]]:
    """Run the middleware once and capture the status + headers + body."""
    captured: dict[str, Any] = {"status": None, "headers": {}, "body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
            captured["headers"] = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body", b"")

    headers: list[tuple[bytes, bytes]] = [(b"host", b"x")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http", "method": "POST", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": headers, "scheme": "http", "server": ("x", 80),
    }
    asyncio.run(mw(scope, receive, send))
    body = json.loads(captured["body"].decode()) if captured["body"] else {}
    return captured["status"], captured["headers"], body


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_valid_token_passes(stub_middleware, rsa_keypair):
    token = _mint_token(rsa_keypair)
    status, _h, body = _invoke(stub_middleware, token=token)
    assert status == 200
    assert body["oid"] == "user-oid-1"
    assert "ARB.Invoke" in body["scopes"]


def test_missing_authorization_header_401(stub_middleware):
    status, headers, _b = _invoke(stub_middleware, token=None)
    assert status == 401
    assert "bearer" in headers["www-authenticate"].lower()


def test_expired_token_401(stub_middleware, rsa_keypair):
    token = _mint_token(rsa_keypair, exp=int(time.time()) - 60)
    status, headers, _b = _invoke(stub_middleware, token=token)
    assert status == 401
    assert "invalid_token" in headers["www-authenticate"]


def test_wrong_audience_401(stub_middleware, rsa_keypair):
    token = _mint_token(rsa_keypair, aud="api://wrong-app")
    status, _h, _b = _invoke(stub_middleware, token=token)
    assert status == 401


def test_wrong_issuer_401(stub_middleware, rsa_keypair):
    token = _mint_token(rsa_keypair, iss="https://login.microsoftonline.com/other-tenant/v2.0")
    status, _h, _b = _invoke(stub_middleware, token=token)
    assert status == 401


def test_missing_scope_403(stub_middleware, rsa_keypair):
    token = _mint_token(rsa_keypair, scp="User.Read")
    status, headers, _b = _invoke(stub_middleware, token=token)
    assert status == 403
    assert "insufficient_scope" in headers["www-authenticate"]


def test_health_endpoint_anonymous(stub_middleware):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    out: dict[str, Any] = {"status": None}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
        elif message["type"] == "http.response.body":
            pass

    # The stub downstream calls get_current_auth() and will fail if /health
    # bypasses auth and there's no AuthContext. So instead we use a separate
    # middleware with a downstream that doesn't require auth.
    cfg = McpConfig()
    cfg.tenant_id = "t"
    cfg.api_client_id = "c"

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = EntraAuthMiddleware(downstream, mcp_config=cfg, protect_path="/api/mcp")
    scope = {
        "type": "http", "method": "GET", "path": "/health", "raw_path": b"/health",
        "query_string": b"", "headers": [(b"host", b"x")], "scheme": "http", "server": ("x", 80),
    }
    asyncio.run(mw(scope, receive, send))
    assert out["status"] == 200
