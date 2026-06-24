"""Entra ID bearer-token validation + On-Behalf-Of (OBO) credential.

Request flow
------------
1. The React SPA signs the user in (MSAL) and sends an access token whose
   audience is THIS backend API (``api://<BACKEND_CLIENT_ID>``) and that carries
   the ``access_as_user`` delegated scope, in the ``Authorization: Bearer``
   header.
2. :func:`validate_bearer_token` validates that token (signature via the tenant
   JWKS, issuer, audience, expiry, required scope).
3. For each downstream Azure call (AI Search, Azure OpenAI / Cognitive Services,
   the Foundry project) the backend exchanges the user's token for a *new*
   downstream token via the OAuth 2.0 OBO flow (:class:`OboCredential`), so the
   call runs in the **signed-in user's** context. RBAC therefore lives on the
   user (and is also granted to this app's service principal for app-only use).

Why a single shared, assertion-reading credential
-------------------------------------------------
The validate pipeline fans retrieval/embedding/agent calls out across a
``ThreadPoolExecutor`` (``loop.run_in_executor``). ``contextvars`` do not
propagate into those threads, so the active user assertion is published through
a plain module-level global (:func:`set_current_assertion`). The shared
:class:`OboCredential` reads that global at ``get_token`` time instead of baking
an assertion in, which means the cached Search/OpenAI/Foundry clients stay valid
across requests (MSAL caches the exchanged tokens per user+scope internally).

Concurrency note: the assertion global is process-wide (last-writer-wins). This
is correct for the single-developer / single-user-at-a-time local pattern. For
concurrent multi-user serving, move the assertion into a request-scoped context
and rebuild the downstream clients per request.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger(__name__)


class AuthError(Exception):
    """Invalid / missing token. Maps to HTTP 401."""

    status_code = 401


class ScopeError(Exception):
    """Valid token but missing the required scope. Maps to HTTP 403."""

    status_code = 403


def _tenant_id() -> str:
    return (os.getenv("ENTRA_TENANT_ID") or os.getenv("AZURE_TENANT_ID") or "").strip()


def _backend_client_id() -> str:
    return (os.getenv("ENTRA_API_CLIENT_ID") or "").strip()


def _backend_secret() -> str:
    return (os.getenv("ENTRA_API_CLIENT_SECRET") or "").strip()


def _audience() -> str:
    aud = (os.getenv("ENTRA_API_AUDIENCE") or "").strip()
    if aud:
        return aud
    cid = _backend_client_id()
    return f"api://{cid}" if cid else ""


def _required_scope() -> str:
    return (os.getenv("ENTRA_REQUIRED_SCOPE") or "access_as_user").strip()


def _issuer() -> str:
    return (
        os.getenv("ENTRA_ISSUER")
        or f"https://login.microsoftonline.com/{_tenant_id()}/v2.0"
    ).strip()


def _allowed_issuers() -> set[str]:
    """Accept both v2.0 and v1.0 issuer forms for this tenant.

    Depending on the API app's ``requestedAccessTokenVersion`` (and any cached
    tokens issued before it was changed), Entra may stamp either issuer:
      - v2.0: ``https://login.microsoftonline.com/<tid>/v2.0``
      - v1.0: ``https://sts.windows.net/<tid>/``
    """
    explicit = (os.getenv("ENTRA_ISSUER") or "").strip()
    tid = _tenant_id()
    issuers = {
        f"https://login.microsoftonline.com/{tid}/v2.0",
        f"https://sts.windows.net/{tid}/",
    }
    if explicit:
        issuers.add(explicit)
    return issuers


def obo_enabled() -> bool:
    """True when Entra OBO is fully configured (tenant + client id + secret)."""
    return bool(_tenant_id() and _backend_client_id() and _backend_secret())


# --------------------------------------------------------------------------- #
# Incoming token validation (JWKS)                                            #
# --------------------------------------------------------------------------- #

_JWK_CLIENT: Any | None = None
_JWK_LOCK = threading.Lock()


def _jwk_client() -> Any:
    global _JWK_CLIENT
    if _JWK_CLIENT is not None:
        return _JWK_CLIENT
    with _JWK_LOCK:
        if _JWK_CLIENT is None:
            from jwt import PyJWKClient

            uri = (
                f"https://login.microsoftonline.com/{_tenant_id()}"
                "/discovery/v2.0/keys"
            )
            # PyJWKClient caches keys in-memory and refreshes on a kid miss.
            _JWK_CLIENT = PyJWKClient(uri, cache_keys=True)
    return _JWK_CLIENT


def validate_bearer_token(token: str) -> dict[str, Any]:
    """Validate an incoming Entra access token; return its claims.

    Raises :class:`AuthError` (401) on any signature/issuer/audience/expiry
    failure and :class:`ScopeError` (403) when the required delegated scope is
    absent.
    """
    import jwt

    if not token:
        raise AuthError("missing bearer token")
    # v1 tokens audience the API by its appId GUID; v2 tokens use api://<id>.
    allowed_aud = {a for a in (_audience(), _backend_client_id()) if a}
    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=list(allowed_aud),
            options={"require": ["exp", "iss", "aud"], "verify_iss": False},
        )
    except Exception as e:  # noqa: BLE001
        log.warning("bearer token rejected: %s", e)
        raise AuthError("invalid_token") from e

    issuer = claims.get("iss", "")
    if issuer not in _allowed_issuers():
        log.warning("bearer token rejected: issuer %r not allowed", issuer)
        raise AuthError("invalid_token")

    scopes = (claims.get("scp") or "").split()
    if _required_scope() not in scopes:
        log.warning(
            "token missing required scope %r (has %r)", _required_scope(), scopes
        )
        raise ScopeError("insufficient_scope")
    return claims


# --------------------------------------------------------------------------- #
# Current-request user assertion (process-wide, thread-visible)               #
# --------------------------------------------------------------------------- #

_assertion_lock = threading.Lock()
_current_assertion: str | None = None


def set_current_assertion(token: str | None) -> None:
    """Publish the active user assertion for downstream OBO exchanges."""
    global _current_assertion
    with _assertion_lock:
        _current_assertion = token


def get_current_assertion() -> str | None:
    with _assertion_lock:
        return _current_assertion


def clear_current_assertion() -> None:
    set_current_assertion(None)


# --------------------------------------------------------------------------- #
# OBO credential                                                              #
# --------------------------------------------------------------------------- #

_CCA: Any | None = None
_CCA_LOCK = threading.Lock()


def _confidential_client() -> Any:
    """MSAL confidential client for this backend app (caches OBO tokens)."""
    global _CCA
    if _CCA is not None:
        return _CCA
    with _CCA_LOCK:
        if _CCA is None:
            import msal

            _CCA = msal.ConfidentialClientApplication(
                client_id=_backend_client_id(),
                authority=f"https://login.microsoftonline.com/{_tenant_id()}",
                client_credential=_backend_secret(),
            )
    return _CCA


class OboCredential:
    """``azure.core`` TokenCredential that performs the OBO exchange.

    Reads the *current* request's user assertion at call time so a single shared
    instance can be cached inside the Search / OpenAI / Foundry clients.
    """

    # Stable id so client caches can key on "the OBO credential" distinctly
    # from the local CLI fallback.
    cache_id = "obo"

    def get_token(self, *scopes: str, **kwargs: Any):  # noqa: D401, ANN401
        from azure.core.credentials import AccessToken

        assertion = get_current_assertion()
        if not assertion:
            raise AuthError("no user assertion in context for OBO exchange")
        if not scopes:
            raise ValueError("get_token requires at least one scope")
        # Azure SDKs pass resource/.default scopes; OBO with .default returns the
        # consented delegated permissions for that resource.
        result = _confidential_client().acquire_token_on_behalf_of(
            user_assertion=assertion,
            scopes=list(scopes),
        )
        if "access_token" not in result:
            err = result.get("error_description") or result.get("error") or result
            raise AuthError(f"OBO exchange failed: {err}")
        expires_on = int(time.time()) + int(result.get("expires_in", 3599))
        return AccessToken(result["access_token"], expires_on)

    # Newer azure-core may call get_token_info; delegate to get_token.
    def get_token_info(self, *scopes: str, **kwargs: Any):  # noqa: ANN401
        from azure.core.credentials import AccessTokenInfo

        tok = self.get_token(*scopes, **kwargs)
        return AccessTokenInfo(tok.token, tok.expires_on)


_SHARED_OBO = OboCredential()


def shared_obo_credential() -> OboCredential:
    return _SHARED_OBO


def current_credential() -> Any:
    """Return the OBO credential when a request assertion is active and OBO is
    configured; otherwise ``None`` so callers fall back to the local picker."""
    if obo_enabled() and get_current_assertion():
        return _SHARED_OBO
    return None


def credential_cache_id(cred: Any) -> str:
    """Stable cache key fragment for a credential, so per-identity clients
    don't collide."""
    return getattr(cred, "cache_id", "default")
