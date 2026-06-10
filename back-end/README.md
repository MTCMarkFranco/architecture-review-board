# ARB Bot — back-end

Flask service exposing `/validatearb` and `/geniac` to the React front-end. Orchestrates **Microsoft Foundry v2 hosted prompt agents** invoked through the **Responses API** with retrieval driven by **Azure AI Search** (hybrid + semantic ranker). Policy ingest is a **pull-mode pipeline**: Content Understanding cracks + chunks blobs, an Azure OpenAI chat skill categorizes each chunk against the canonical `PolicyCategory` taxonomy, and an Azure OpenAI embedding skill vectorizes them — all server-side via a Search skillset, no Python push code. All Azure access uses identity-based auth — `AzureCliCredential` locally (skips the IMDS probe), `DefaultAzureCredential` in Azure-hosted runtimes. A module-level `SearchClient` cache in `search/query.py` keeps per-call overhead minimal under the section × category fan-out.

See the [repo root README](../README.md) for the full end-to-end setup. This file documents back-end specifics.

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Flask app + endpoint handlers |
| `agents/orchestrator.py` | `ArbWorkflow.validate` / `.iac` with retry + circuit breaker |
| `agents/validate_agent.py` | Responses-API client; `_retrieve_for_section` + prompt assembly |
| `agents/iac_agent.py` | IaC generator client |
| `agents/categories.py` | Canonical `PolicyCategory` enum + ASD/IaC section mappings + AOAI prompt |
| `agents/config.py` | env-driven `Config` (auto-loads repo-root `.env`) |
| `search/build_indexer.py` | Provisions data source + skillset + indexer; optional `--run` / `--purge` / `--status` |
| `search/skillset_definition.json` | CU + AOAI categorize + AOAI embed + index projections |
| `search/indexer_definition.json` | Indexer that glues data source → skillset → index |
| `search/datasource_definition.json` | Blob data source (managed identity auth) |
| `search/query.py` | Hybrid + semantic search wrapper |
| `search/categorize.py` | Legacy keyword fallback only — superseded by the AOAI skill |
| `infra/provision.py` | Idempotent Foundry + Search provisioning (chains the pipeline provisioner) |
| `infra/provision_search_pipeline.py` | Idempotent storage account + container + RBAC for the pull pipeline (grants the search MI `Storage Blob Data Reader` on the storage account and `Cognitive Services User` on the Foundry account) |
| `infra/create_agents.py` | Create/version `ValidateArbAgent` + `IacGeneratorAgent` |
| `file_processing/parsing.py` | docx + pdf parsing |
| `tests/` | pytest suite |

## Prerequisites

- Python 3.11+ (3.14 tested)
- `az login` against the target subscription
- RBAC on the RG, Search service, and storage account (see root README)
- Repo-root `.env` (auto-loaded — see root README)

## Install + run

```powershell
cd back-end
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# First time only (each command is idempotent):
python infra\provision.py             # provision Foundry + Search + Storage + RBAC

# Upload the source policy doc into the blob container:
az storage blob upload `
  --account-name <storage-account-from-env-example> `
  --container-name arb-policies-source `
  --name azure_policies.docx `
  --file file_processing\data\azure_policies.docx `
  --auth-mode login `
  --overwrite

# Provision + run the pull-mode indexer (idempotent):
python -m search.build_indexer --run

python -m infra.create_agents         # create hosted prompt agents

# Daily:
python app.py                         # serves on http://127.0.0.1:5000
```

`provision.py` writes resolved resource IDs to `back-end/.env.example`. Copy any new values into the repo-root `.env`.

## Environment variables

All are read from the repo-root `.env` via `python-dotenv`. The required runtime set:

| Variable | Source | Notes |
|---|---|---|
| `FOUNDRY_LOCATION` | manual | e.g. `canadacentral`, `canadaeast` |
| `FOUNDRY_MODEL` | manual | e.g. `gpt-5.3-chat`, `gpt-5.4` |
| `AZURE_SUBSCRIPTION_ID` | `az account show` | |
| `AZURE_TENANT_ID` | `az account show` | |
| `FOUNDRY_PROJECT_ENDPOINT` | provision.py output | Foundry project URL |
| `FOUNDRY_MODEL_DEPLOYMENT` | provision.py output | deployment name (e.g. `gpt-5.3-chat-1`) |
| `FOUNDRY_CATEGORIZE_DEPLOYMENT` | optional | dedicated faster deployment for per-chunk categorize (e.g. `gpt-5.4-mini`); falls back to `FOUNDRY_MODEL_DEPLOYMENT` |
| `FOUNDRY_EMBEDDINGS_DEPLOYMENT` | provision.py output | `text-embedding-3-large` |
| `FOUNDRY_ENDPOINT` | provision.py output | account endpoint (for embeddings + chat) |
| `FOUNDRY_CU_ENDPOINT` | manual | **Required.** Foundry v2 AI Services subdomain (`https://<account>.services.ai.azure.com/`) used as the skillset billing target. Do **not** use the `cognitiveservices.azure.com` subdomain — it fails `AIServicesByIdentity` validation. Set this to a CU-supported region (Sweden Central, East US 2, West US 3) if CU is unavailable in your Foundry region |
| `AZURE_SEARCH_ENDPOINT` | provision.py output | search service URL |
| `AZURE_SEARCH_INDEX` | optional | defaults to `arb-policies` |
| `STORAGE_ACCOUNT_RESOURCE_ID` | provision.py output | full ARM id of the source-blob storage account |
| `STORAGE_CONTAINER` | optional | defaults to `arb-policies-source` |
| `VALIDATE_AGENT_NAME` | optional | defaults to `ValidateArbAgent` |
| `IAC_AGENT_NAME` | optional | defaults to `IacGeneratorAgent` |
| `WORKFLOW_TIMEOUT_SECONDS` | optional | defaults to `60`; bump to `180` for full-doc runs |
| `MISSING_VERIFY_ENABLED` | optional | defaults to `true`; doc-level verification of deduped `Missing` findings. Set `false` to skip the extra fast-model calls and keep raw `(also missing in N other chunks)` suffixes |
| `MISSING_VERIFY_MAX` | optional | defaults to `10`; cap on distinct `Missing` principles verified per validate run. Tail items beyond the cap keep the chunk-count suffix |

## Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/validatearb` | `multipart/form-data` field `file` (`.pdf` / `.docx`) | JSON array of finding objects |
| POST | `/geniac` | same | JSON array of Terraform script strings |

## Tests

```powershell
cd back-end
pytest -q
```

- Unit tests run with no Azure credentials.
- Integration tests (`@pytest.mark.integration`) auto-skip when `FOUNDRY_ENDPOINT` / `AZURE_SEARCH_ENDPOINT` / `STORAGE_ACCOUNT_RESOURCE_ID` are missing.

## Useful one-liners

Re-ingest after editing the policy docx:
```powershell
# 1. Re-upload the docx (overwrites the blob)
az storage blob upload --account-name <acct> --container-name arb-policies-source `
  --name azure_policies.docx --file file_processing\data\azure_policies.docx `
  --auth-mode login --overwrite
# 2. Purge stale chunks and re-run the indexer
python -m search.build_indexer --purge --run
```

Bump a new agent version after changing the system prompt:
```powershell
python -m infra.create_agents
```

Dry-run provisioning to see what would change:
```powershell
python infra\provision.py --dry-run
```

Inspect the last indexer run without re-running:
```powershell
python -m search.build_indexer --status
```

## MCP server (contracts MCP-SERVER-ENTRA #90, MCP-SHAREPOINT-OBO #91)

The Flask REST endpoints (`/validatearb`, `/geniac`, `/health`) are unchanged.
**Additive**: a Model Context Protocol server is mounted at
`MCP_ROUTE` (default `/api/mcp`) exposing the same capabilities to
Microsoft Copilot Studio and other MCP clients.

### Tools / resources / prompts

| Tool | Purpose |
|---|---|
| `validate_arb` | Validate an ASD (`file_bytes_b64` OR SharePoint `file_reference`) |
| `generate_iac` | Produce Terraform for an approved design (same input shapes) |
| `search_policies` | Hybrid + semantic search of `arb-policies` |
| `list_policy_categories` | Return the canonical `PolicyCategory` list |

Resources: `arb://policies`, `arb://policies/{id}`.
Prompts: `review_architecture`, `explain_finding`, `draft_iac`.

### Required env vars

| Var | Source | Notes |
|---|---|---|
| `MCP_SERVER_NAME` | optional | defaults to `arb-bot-mcp` |
| `MCP_ROUTE` | optional | defaults to `/api/mcp` |
| `ENTRA_TENANT_ID` | provision_mcp_entra.py output | |
| `ENTRA_API_CLIENT_ID` | provision_mcp_entra.py output | App registration that exposes the MCP API |
| `ENTRA_API_AUDIENCE` | optional | defaults to `api://<ENTRA_API_CLIENT_ID>` |
| `ENTRA_REQUIRED_SCOPE` | optional | defaults to `ARB.Invoke` |
| `ENTRA_API_CLIENT_SECRET` | **Key Vault / App Service config** | Required for OBO; never commit |
| `GRAPH_SCOPES` | optional | defaults to `Files.Read.All Sites.Read.All` |
| `MCP_MAX_BODY_BYTES` | optional | defaults to 25 MiB |
| `MCP_CORS_ORIGINS` | optional | comma-separated allow-list; no wildcards in prod |

### Local run

```powershell
# 1. Provision Entra app registration + scope + Graph permissions (idempotent)
python -m infra.provision_mcp_entra              # plan + apply
python -m infra.provision_mcp_entra --dry-run    # plan only
python -m infra.provision_mcp_entra --issue-secret  # rotate / issue client secret (printed once)

# 2. Set env (FOUNDRY_/AZURE_ vars must already be set per the table above)
$env:ENTRA_API_CLIENT_SECRET = "<from Key Vault>"

# 3. Run the composite ASGI app (MCP + legacy Flask routes)
python -m mcp_server.server
# or:
uvicorn mcp_server.server:_lazy_app --factory --host 0.0.0.0 --port 8000
```

The MCP route is at `http://localhost:8000/api/mcp` (Streamable HTTP +
SSE). `/validatearb`, `/geniac`, `/health` are still served by Flask.

### Test with MCP Inspector

```powershell
$token = az account get-access-token --resource "api://<ENTRA_API_CLIENT_ID>" --query accessToken --output tsv
# Use the bearer token in MCP Inspector or any MCP client.
```

### Test with raw curl / Invoke-WebRequest

```powershell
$token = az account get-access-token --resource "api://<appId>" --query accessToken --output tsv
$h = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json"; Accept = "application/json, text/event-stream" }
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
Invoke-WebRequest -Uri "http://localhost:8000/api/mcp" -Method POST -Headers $h -Body $body
```

### SharePoint file mentions (OBO)

When Copilot Studio sends a `file_reference` (driveId/itemId, siteId/itemId,
or webUrl/sharing link) instead of inline bytes, the server:

1. Validates the incoming user token (`mcp_server/auth.py`)
2. Exchanges it for a Microsoft Graph delegated token via MSAL
   `acquire_token_on_behalf_of` (OBO)
3. Resolves the locator to a drive item and downloads `/content`
4. Feeds bytes to `ArbWorkflow.validate_bytes` / `iac_bytes` unchanged

Per-user SharePoint permissions are enforced end-to-end. A user without
access gets `file_access_denied`; the server **never** retries with
app-only / service-identity. See `prompt-contracts/MCP-SHAREPOINT-OBO.md`.

### Deploy to App Service

The composite ASGI app (`mcp_server.server:_lazy_app`) runs on any ASGI
host. For Azure App Service Linux + Python:

```bash
gunicorn -k uvicorn.workers.UvicornWorker --factory mcp_server.server:_lazy_app
```

App Service Easy Auth is **optional** — the in-app middleware
(`mcp_server/auth.py`) validates Entra bearer tokens directly. If you
front the app with Easy Auth, set it to **"return 401" for unauthenticated
API calls** (NOT redirect-to-login) and keep the in-app middleware: Easy
Auth handles the connection-level audit, the middleware enforces the
`ARB.Invoke` scope.

### Copilot Studio integration

See `back-end/mcp_server/copilot-studio/README.md`. Edit
`mcp-connector.openapi.yaml` to fill your `https://YOUR-APP.azurewebsites.net`,
`<TENANT_ID>`, and `<ENTRA_API_CLIENT_ID>`, then import via Copilot
Studio → Tools → Custom connector → Import OpenAPI.

