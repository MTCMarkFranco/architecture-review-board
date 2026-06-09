# ARB Bot — back-end

Flask service exposing `/validatearb` and `/geniac` to the React front-end. Orchestrates **Microsoft Foundry v2 hosted prompt agents** invoked through the **Responses API** with retrieval driven by **Azure AI Search** (hybrid + semantic ranker). All Azure access uses identity-based auth — `AzureCliCredential` locally (skips the IMDS probe), `DefaultAzureCredential` in Azure-hosted runtimes. A module-level `SearchClient` cache in `search/query.py` keeps per-call overhead minimal under the section × category fan-out.

See the [repo root README](../README.md) for the full end-to-end setup. This file documents back-end specifics.

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Flask app + endpoint handlers |
| `agents/orchestrator.py` | `ArbWorkflow.validate` / `.iac` with retry + circuit breaker |
| `agents/validate_agent.py` | Responses-API client; `_retrieve_for_section` + prompt assembly |
| `agents/iac_agent.py` | IaC generator client |
| `agents/config.py` | env-driven `Config` (auto-loads repo-root `.env`) |
| `search/build_index.py` | Create/update the `arb-policies` index + ingest the docx |
| `search/query.py` | Hybrid + semantic search wrapper |
| `search/categorize.py` | Header → category routing rules |
| `infra/provision.py` | Idempotent Azure provisioning (Foundry + Search) |
| `infra/create_agents.py` | Create/version `ValidateArbAgent` + `IacGeneratorAgent` |
| `file_processing/parsing.py` | docx + pdf parsing |
| `tests/` | pytest suite |

## Prerequisites

- Python 3.11+ (3.14 tested)
- `az login` against the target subscription
- RBAC on the RG / Search service (see root README)
- Repo-root `.env` (auto-loaded — see root README)

## Install + run

```powershell
cd back-end
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# First time only (each command is idempotent):
python infra\provision.py           # provision Foundry + Search
python -m search.build_index        # build the arb-policies index
python -m infra.create_agents       # create hosted prompt agents

# Daily:
python app.py                       # serves on http://127.0.0.1:5000
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
| `FOUNDRY_EMBEDDINGS_DEPLOYMENT` | provision.py output | `text-embedding-3-large` |
| `FOUNDRY_ENDPOINT` | provision.py output | account endpoint (for embeddings) |
| `AZURE_SEARCH_ENDPOINT` | provision.py output | search service URL |
| `AZURE_SEARCH_INDEX` | optional | defaults to `arb-policies` |
| `VALIDATE_AGENT_NAME` | optional | defaults to `ValidateArbAgent` |
| `IAC_AGENT_NAME` | optional | defaults to `IacGeneratorAgent` |
| `WORKFLOW_TIMEOUT_SECONDS` | optional | defaults to `60`; bump for full-doc runs |

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
- Integration tests (`@pytest.mark.integration`) auto-skip when `FOUNDRY_ENDPOINT` / `AZURE_SEARCH_ENDPOINT` are missing.

## Useful one-liners

Re-ingest the policy docx after edits:
```powershell
python -m search.build_index
```

Bump a new agent version after changing the system prompt:
```powershell
python -m infra.create_agents
```

Dry-run provisioning to see what would change:
```powershell
python infra\provision.py --dry-run
```
