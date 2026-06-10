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
