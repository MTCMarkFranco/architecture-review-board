# Copilot Studio — ARB Bot MCP connector

This folder contains the artifacts a Copilot Studio maker needs to add the
ARB Bot MCP server as an OAuth-protected skill on a custom agent.

## What gets added

- **`mcp-connector.openapi.yaml`** — OpenAPI 3.0 description of the single
  MCP route (`POST /api/mcp`) plus the Entra OAuth 2.0 (authorization-code
  with PKCE) security scheme. Copilot Studio's custom connector importer
  understands this shape directly.

## Prerequisites

1. The MCP server is deployed (App Service / Container Apps / k8s — see
   `back-end/README.md` "MCP server" section).
2. The Entra App Registration was created with
   `python -m infra.provision_mcp_entra`. You'll need:
   - `ENTRA_TENANT_ID`
   - `ENTRA_API_CLIENT_ID`
   - the exposed scope name (default `ARB.Invoke`)
   - a client secret (issue with `--issue-secret` and store in Key Vault)
3. Admin consent has been granted for the delegated Microsoft Graph
   permissions `Files.Read.All` and `Sites.Read.All` (the provisioning
   script attempts this automatically; if your identity lacks
   `Application.ReadWrite.All`, a tenant admin must click the consent URL
   the script prints).
4. The Azure CLI (or any client whose appId is **pre-authorized** on the
   API app) can acquire a token for `api://<ENTRA_API_CLIENT_ID>/.default`.
   Copilot Studio's first-party connector framework registers its own
   client id when you set up the connection; consent fires once per maker.

## Steps

1. **Open `mcp-connector.openapi.yaml`** and replace the three placeholders:
   - `https://YOUR-APP.azurewebsites.net` → your deployed hostname
   - `<TENANT_ID>` → your Entra tenant id
   - `<ENTRA_API_CLIENT_ID>` → the API app registration's `appId`
2. In Copilot Studio, open **Tools** → **+ New tool** → **Custom connector**
   → **Import an OpenAPI file**, and select the edited YAML.
3. Configure the connector security:
   - Client id: provided by Copilot Studio at connector creation
   - Client secret: paste the value you stored in Key Vault
   - Resource URL: `api://<ENTRA_API_CLIENT_ID>`
4. Click **Create connection**, consent once, then **Add tool** to attach
   the connector to your agent.
5. Add the prompt `review_architecture` (advertised by the MCP server via
   `prompts/list`) to your agent's entry intents so users can `/`-mention
   an ASD and have it validated.

## SharePoint file mentions

When a user `/`-mentions a SharePoint file in Copilot Studio chat, the
connector forwards a `file_reference` object to `validate_arb` /
`generate_iac`. The MCP server then performs an OAuth On-Behalf-Of
exchange (`acquire_token_on_behalf_of`) to download the file **as the
caller** via Microsoft Graph. Per-user SharePoint permissions are
enforced end-to-end — if the user can't read the file in SharePoint, the
tool returns `file_access_denied`. The server never falls back to
app-only access.

See `prompt-contracts/MCP-SHAREPOINT-OBO.md` for the full design.

## Smoke test (local, without Copilot Studio)

After running `python -m mcp_server.server` and getting an Entra token:

```powershell
$token = az account get-access-token --resource "api://<appId>" --query accessToken --output tsv
$h = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json"; Accept = "application/json, text/event-stream" }
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/mcp" -Method POST -Headers $h -Body $body
```

Or use the official [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
pointed at `http://127.0.0.1:8000/api/mcp` with the bearer token.
