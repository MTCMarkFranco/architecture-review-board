"""Runtime configuration for the ARB Bot MCP server.

All Entra/MCP env vars are read from process env (with ``.env`` already
loaded by ``app.py``/``agents.config``). Values are non-secret IDs only —
the client secret for OBO is read at the call site from a Key Vault
reference or App Service config, never persisted here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class McpConfig:
    server_name: str = field(default_factory=lambda: _env("MCP_SERVER_NAME", "arb-bot-mcp"))
    route: str = field(default_factory=lambda: _env("MCP_ROUTE", "/api/mcp"))

    # Entra (API app registration that exposes the MCP API).
    tenant_id: str = field(default_factory=lambda: _env("ENTRA_TENANT_ID"))
    api_client_id: str = field(default_factory=lambda: _env("ENTRA_API_CLIENT_ID"))
    api_audience: str = field(default_factory=lambda: _env("ENTRA_API_AUDIENCE"))
    required_scope: str = field(default_factory=lambda: _env("ENTRA_REQUIRED_SCOPE", "ARB.Invoke"))
    issuer_override: str = field(default_factory=lambda: _env("ENTRA_ISSUER"))

    # OBO / Graph.
    graph_scopes: str = field(default_factory=lambda: _env("GRAPH_SCOPES", "Files.Read.All Sites.Read.All"))
    graph_base_url: str = field(default_factory=lambda: _env("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0"))

    # Limits.
    max_body_bytes: int = field(default_factory=lambda: int(_env("MCP_MAX_BODY_BYTES", str(25 * 1024 * 1024))))
    cors_origins: str = field(default_factory=lambda: _env("MCP_CORS_ORIGINS", ""))

    @property
    def issuer(self) -> str:
        if self.issuer_override:
            return self.issuer_override
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def accepted_issuers(self) -> tuple[str, ...]:
        """Both v1 and v2 issuer URLs for the configured tenant.

        Entra issues v1 tokens (``sts.windows.net``) for legacy apps and v2
        tokens (``login.microsoftonline.com/.../v2.0``) for apps with
        ``requestedAccessTokenVersion=2``. We accept either so a
        deployment can roll forward without breaking in-flight tokens.
        """
        if self.issuer_override:
            return (self.issuer_override,)
        return (
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
            f"https://sts.windows.net/{self.tenant_id}/",
        )

    @property
    def jwks_uri(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    @property
    def effective_audience(self) -> str:
        return self.api_audience or (f"api://{self.api_client_id}" if self.api_client_id else "")

    def require_runtime(self) -> None:
        missing = []
        if not self.tenant_id:
            missing.append("ENTRA_TENANT_ID")
        if not self.api_client_id:
            missing.append("ENTRA_API_CLIENT_ID")
        if missing:
            raise RuntimeError(
                f"MCP server is misconfigured. Missing env vars: {', '.join(missing)}. "
                "Run `python -m infra.provision_mcp_entra` to provision them."
            )
