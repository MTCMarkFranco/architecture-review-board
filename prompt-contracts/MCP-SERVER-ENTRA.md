# MCP-SERVER-ENTRA — Prompt Contract

## Intent

Transform the existing ARB Bot Flask back-end (`back-end/app.py`) into a **robust, stateless MCP server** that exposes the ARB validation, IaC generation, and policy-search capabilities as MCP **tools**, the `arb-policies` Azure AI Search index as an MCP **knowledge source** (resources), and a set of reusable MCP **prompts**. The server must be protected by **Microsoft Entra ID** so it can be consumed by **Microsoft Copilot Studio as a custom connector / skill over OAuth 2.0** (authorization-code + PKCE for delegated user calls). The HTTP surface follows the App Service MCP pattern from the [Node tutorial](https://learn.microsoft.com/en-us/azure/app-service/tutorial-ai-model-context-protocol-server-node) (stateless Streamable HTTP at `/api/mcp`), adapted to the project's Python stack using the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). The existing REST endpoints (`/validatearb`, `/geniac`, `/health`) are retained for backward compatibility; the MCP route is additive.

## Linked issue

**#90** — Expose ARB Bot as an Entra-protected MCP server for Copilot Studio · `branch-mcp-server-entra-1`

## Inputs

- Existing agent surface (reused, **not** duplicated):
  - `agents.orchestrator.ArbWorkflow.validate_bytes(file_bytes, filename)`
  - `agents.orchestrator.ArbWorkflow.iac_bytes(file_bytes, filename)`
  - `search.query` (hybrid + semantic search over `arb-policies`, from `SEARCH-REFACTOR`)
  - `agents.categories` (category list, for the `list_policy_categories` tool)
- Env vars (new, non-secret IDs written by provisioning):
  - `MCP_SERVER_NAME` (default `arb-bot-mcp`)
  - `MCP_ROUTE` (default `/api/mcp`)
  - `ENTRA_TENANT_ID`
  - `ENTRA_API_CLIENT_ID` — app registration **exposing** the API (audience).
  - `ENTRA_API_AUDIENCE` — `api://<ENTRA_API_CLIENT_ID>` (accepted `aud`).
  - `ENTRA_REQUIRED_SCOPE` (default `ARB.Invoke`) — delegated scope the token must carry.
  - `ENTRA_ISSUER` (default `https://login.microsoftonline.com/<tenant>/v2.0`).
- Existing: `FOUNDRY_PROJECT_ENDPOINT`, `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX`, `DefaultAzureCredential`.
- A logged-in `az` session for the provisioning helper.

## Outputs

- `back-end/mcp/__init__.py`
- `back-end/mcp/server.py` — builds a **stateless** MCP server (fresh instance per request) and exposes it as an ASGI/WSGI-mountable app at `MCP_ROUTE`. Mounted into the existing Flask app (via WSGI middleware) or served alongside it; `/validatearb`, `/geniac`, `/health` remain unchanged.
- `back-end/mcp/auth.py` — Entra bearer-token validation middleware:
  - Fetches and caches the tenant JWKS from the OIDC discovery document.
  - Validates signature, `iss`, `aud` (== `ENTRA_API_AUDIENCE`), `exp`/`nbf`, and that the `scp` claim contains `ENTRA_REQUIRED_SCOPE`.
  - On failure returns RFC 6750 `401` with `WWW-Authenticate: Bearer error="invalid_token"` (never `200` with an error body).
  - Exposes the validated token + claims to downstream tools via a request-scoped context (consumed by `MCP-SHAREPOINT-OBO`).
- `back-end/mcp/tools.py` — tool registrations (one thin wrapper per capability; **no business logic duplicated** — they call the orchestrator/search modules):
  - `validate_arb` — input: base64 file bytes **or** a SharePoint file reference (see `MCP-SHAREPOINT-OBO`) + `filename`; output: findings JSON.
  - `generate_iac` — input: same; output: list of IaC scripts.
  - `search_policies` — input: `query`, optional `category`, optional `source_doc`, `top`; output: hybrid+semantic search hits.
  - `list_policy_categories` — output: the canonical category list from `agents.categories`.
- `back-end/mcp/resources.py` — MCP **knowledge source** (resources) backed by `arb-policies`:
  - `arb://policies` (list) and `arb://policies/{id}` (single policy document) resource templates that read from the search index, so Copilot Studio can ground answers on ARB policy text.
- `back-end/mcp/prompts.py` — MCP **prompts**:
  - `review_architecture` — guided ARB review of an uploaded/mentioned ASD.
  - `explain_finding` — explain a single validation finding with its policy citation.
  - `draft_iac` — produce IaC for an approved design.
- `back-end/infra/provision_mcp_entra.py` — idempotent helper that creates/reuses the Entra **API app registration**, exposes the `ENTRA_REQUIRED_SCOPE` scope, configures App Service Easy Auth (token-store off, "return 401" for unauthenticated API calls), and writes the non-secret IDs above into `back-end/.env.example`.
- `back-end/mcp/copilot-studio/` — connector artifacts for Copilot Studio:
  - `mcp-connector.openapi.yaml` (or `apiProperties.json`) describing the streamable MCP endpoint + OAuth 2.0 (auth-code) security scheme pointing at the Entra authorize/token endpoints and the `ENTRA_REQUIRED_SCOPE`.
  - `README.md` — step-by-step: register the connector, set client id/secret, consent, and add the skill to an agent.
- `back-end/requirements.txt` updated with `mcp` (Python SDK), `pyjwt[crypto]` (or `azure-identity` token validation deps).
- `back-end/README.md` — new "MCP server" section: run locally, test with VS Code agent mode / MCP Inspector, deploy to App Service, and wire into Copilot Studio.
- `back-end/tests/test_mcp_auth.py`, `back-end/tests/test_mcp_tools.py` — unit tests (token validation matrix + tool wiring with a fake orchestrator).

## Edge cases & clarifications

1. **Missing/expired/malformed bearer token** → `401` with `WWW-Authenticate: Bearer`; no tool executes and no stack trace leaks to the caller.
2. **Valid signature but wrong audience or issuer** → `401 invalid_token`; log the rejected `aud`/`iss` at WARNING (do not log the raw token).
3. **Token valid but missing `ENTRA_REQUIRED_SCOPE`** → `403 insufficient_scope` (RFC 6750), distinct from `401`.
4. **JWKS endpoint unreachable / key rotation** → cache last-good keys with TTL; on a `kid` miss, force a single JWKS refresh before rejecting; fail closed (reject) if still unresolved.
5. **Stateless per-request server** → a new `McpServer`/transport is created per request (`sessionIdGenerator=None`) and closed on `res.on('close')`-equivalent; no cross-request session state, matching the tutorial's stateless pattern.
6. **Large file payloads** → enforce a configurable max body size; reject oversized base64 with `413`; prefer SharePoint reference path (`MCP-SHAREPOINT-OBO`) for big files.
7. **Tool input validation** → every tool input is schema-validated (pydantic/zod-equivalent); reject unexpected fields; sanitize `filename` (no path traversal) before passing to the parser.
8. **Prompt-injection hardening** → tool/resource descriptions are static and minimal (least privilege); retrieved policy text is treated as data, never as instructions; document this in the README per the tutorial's security guidance.
9. **App Service Easy Auth vs in-app validation** → support both: when Easy Auth fronts the app the middleware trusts `X-MS-CLIENT-PRINCIPAL`-injected token but **still** validates `aud`/`scp`; when running locally Easy Auth is absent and `auth.py` validates the raw `Authorization` header.
10. **CORS** → MCP route restricted to the Copilot Studio / trusted origins only; no wildcard `*` in production.
11. **Health endpoint stays anonymous** → `/health` is excluded from auth; `MCP_ROUTE` is always protected.
12. **Backward compatibility** → existing `/validatearb` and `/geniac` REST contracts and JSON shapes are unchanged; MCP tools return the same finding/IaC structures so both paths stay in sync.

## Acceptance criteria

- [ ] `POST <url>/api/mcp` speaks MCP over Streamable HTTP (stateless) and lists `validate_arb`, `generate_iac`, `search_policies`, `list_policy_categories` via `tools/list`.
- [ ] `resources/list` returns `arb://policies` and resolves `arb://policies/{id}` from the `arb-policies` index.
- [ ] `prompts/list` returns `review_architecture`, `explain_finding`, `draft_iac`.
- [ ] All MCP tools call the existing orchestrator/search modules — **no business logic is copied** into `back-end/mcp/`.
- [ ] Unauthenticated calls to `/api/mcp` get `401` with `WWW-Authenticate: Bearer`; missing-scope tokens get `403 insufficient_scope`; `/health` stays anonymous.
- [ ] Token validation enforces signature, `iss`, `aud == ENTRA_API_AUDIENCE`, expiry, and `scp ∋ ENTRA_REQUIRED_SCOPE`, with JWKS caching + single-refresh-on-miss.
- [ ] `provision_mcp_entra.py` is idempotent (second run performs zero Entra/App Service writes) and writes only non-secret IDs to `.env.example`.
- [ ] `mcp/copilot-studio/` connector + README let a Copilot Studio maker add the server as an OAuth skill end-to-end.
- [ ] `back-end/README.md` documents local run, MCP Inspector test, App Service deploy, and Copilot Studio wiring.
- [ ] `test_mcp_auth.py` covers valid / expired / wrong-aud / wrong-iss / missing-scope; `test_mcp_tools.py` asserts each tool delegates to the orchestrator/search layer.
