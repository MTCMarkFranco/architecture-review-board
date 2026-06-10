# MCP-SHAREPOINT-OBO — Prompt Contract

## Intent

Enable the Entra-protected ARB Bot MCP server (`MCP-SERVER-ENTRA`) to accept **SharePoint file references** that a Microsoft Copilot Studio agent passes when a user "`/`-mentions" a file in chat. Copilot Studio sends a **JSON structure that references the file** (drive/site/item identifiers) rather than the bytes. The MCP server must resolve and download that file **as the invoking user** — never with an app-only or service identity — by taking the caller's delegated access token (already validated by `mcp/auth.py`) and performing an **OAuth 2.0 On-Behalf-Of (OBO) flow** to exchange it for a Microsoft Graph token, then calling Graph to read the file. The downloaded bytes feed straight into the existing `validate_arb` / `generate_iac` tools, so per-user SharePoint permissions are honored end-to-end.

## Linked issue

**#91** — Resolve Copilot Studio SharePoint file mentions via Graph using OBO · `branch-mcp-sharepoint-obo-1`

## Inputs

- The validated incoming **delegated** access token + claims exposed by `mcp/auth.py` (request-scoped context from `MCP-SERVER-ENTRA`). The token's audience is the ARB API app; it must be OBO-exchangeable for Graph.
- The Copilot Studio file-reference JSON attached to the tool call. Accept the documented shapes and normalize them, e.g.:
  ```json
  {
    "name": "sample-asd.docx",
    "reference": {
      "driveId": "b!....",
      "itemId": "01ABC...",
      "siteId": "contoso.sharepoint.com,<guid>,<guid>",
      "webUrl": "https://contoso.sharepoint.com/sites/ARB/Shared%20Documents/sample-asd.docx"
    }
  }
  ```
  At minimum one resolvable locator must be present: `(driveId + itemId)`, `(siteId + itemId)`, **or** a SharePoint `webUrl`/sharing link.
- Env vars (new):
  - `ENTRA_API_CLIENT_ID` (reused) — the confidential client performing OBO.
  - `ENTRA_API_CLIENT_SECRET` **or** federated/managed-identity credential — secret pulled from Key Vault / App Service config, **never** committed.
  - `ENTRA_TENANT_ID` (reused).
  - `GRAPH_SCOPES` (default `Files.Read.All Sites.Read.All`) — delegated Graph scopes requested in the OBO exchange.
  - `GRAPH_BASE_URL` (default `https://graph.microsoft.com/v1.0`).
- The Entra API app registration must have the above **delegated** Graph permissions granted (admin consent) and be configured to allow OBO.

## Outputs

- `back-end/mcp/sharepoint.py`:
  - `parse_file_reference(payload) -> FileRef` — validates/normalizes the Copilot Studio JSON into a `driveId/itemId/siteId/webUrl` model; raises a typed error on ambiguous/empty references.
  - `acquire_graph_token_obo(user_assertion) -> str` — MSAL `ConfidentialClientApplication.acquire_token_on_behalf_of` exchange using the incoming user token as the assertion; caches per-user tokens in MSAL's token cache; refreshes on expiry.
  - `download_file(file_ref, graph_token) -> (bytes, filename, content_type)` — resolves the locator to a Graph drive-item and streams `/content`; follows `webUrl`/sharing-link → drive-item resolution when only a URL is provided.
- `back-end/mcp/tools.py` (extended): `validate_arb` and `generate_iac` accept **either** inline base64 bytes **or** a `file_reference` object; when a reference is supplied they call `sharepoint.download_file` (after OBO) and then pass the bytes to the orchestrator unchanged.
- `back-end/mcp/auth.py` (extended): surfaces the raw validated user assertion (the bearer JWT) to tools through the request context for use as the OBO `assertion`.
- `back-end/infra/provision_mcp_entra.py` (extended): adds the delegated Graph permissions (`Files.Read.All`, `Sites.Read.All`), prints the admin-consent URL, and notes the client-secret/federated-credential setup. No secret written to `.env.example`.
- `back-end/requirements.txt` updated with `msal` and `httpx` (or reuse existing HTTP client) for the Graph call.
- `back-end/README.md` — "SharePoint file mentions (OBO)" section: required Graph permissions, admin consent, secret placement (Key Vault / App config), and the end-to-end flow diagram.
- `back-end/tests/test_mcp_sharepoint.py` — unit tests for reference parsing, OBO token exchange (mocked MSAL), and Graph download (mocked Graph), including the permission-denied path.

## Edge cases & clarifications

1. **Missing/ambiguous reference** → return a tool error (`invalid_file_reference`) listing which locator fields are required; do not guess or fall back to app-only access.
2. **OBO exchange fails (`invalid_grant` / consent required / token not exchangeable)** → return a clear `403 obo_consent_required` with the admin-consent remediation; never silently downgrade to app-only.
3. **User lacks permission to the file** → Graph returns `403`/`404`; surface as `file_access_denied` (the per-user permission boundary is the whole point — do **not** retry with elevated identity).
4. **`webUrl` / sharing link only** → resolve via Graph (`/shares/{encoded-url}/driveItem`) before downloading; URL-encode per Graph sharing-link rules.
5. **Large files** → stream `/content` (no full in-memory duplication beyond what the parser needs); enforce the same max-size guard as `MCP-SERVER-ENTRA`; reject with `413` past the limit.
6. **Unsupported extension** → only `.pdf`/`.docx` proceed to the orchestrator (matches existing `_parse_uploaded`); other types return `unsupported_file_type` before any download cost where the name is known.
7. **Token expiry mid-request** → MSAL cache refresh; if the *incoming* user assertion is itself expired, the request should have already been rejected by `mcp/auth.py` (`401`).
8. **Throttling (Graph `429`)** → honor `Retry-After`, bounded exponential backoff, capped retries; then surface `graph_throttled`.
9. **Secret hygiene** → client secret loaded from Key Vault/App Service settings only; never logged, never in `.env.example`; prefer a federated credential / managed identity where supported.
10. **Tenant/guest mismatch** → if the file's tenant differs from the token tenant (cross-tenant), fail with `cross_tenant_not_supported` unless explicitly configured.
11. **Audit logging** → log (user oid, file driveId/itemId, action, result) at INFO for traceability; never log token values or file contents.

## Acceptance criteria

- [ ] `validate_arb` / `generate_iac` accept a `file_reference` object and, given a valid user token, download the file **as that user** and produce findings/IaC identical to the inline-bytes path.
- [ ] File download uses a Graph token obtained via MSAL `acquire_token_on_behalf_of` with the incoming user JWT as the assertion — verified by `test_mcp_sharepoint.py` (mocked MSAL).
- [ ] A user without access to the referenced file gets `file_access_denied`; the server never retries with app-only/service identity.
- [ ] `(driveId+itemId)`, `(siteId+itemId)`, and `webUrl`/sharing-link references all resolve to a downloadable drive-item.
- [ ] OBO consent/permission failures return `obo_consent_required` with actionable remediation; no silent app-only fallback.
- [ ] No client secret or token value is logged or written to `.env.example`; secret sourced from Key Vault / App Service config.
- [ ] `provision_mcp_entra.py` adds delegated `Files.Read.All` + `Sites.Read.All` and emits the admin-consent URL.
- [ ] `back-end/README.md` documents permissions, consent, secret placement, and the OBO flow.
