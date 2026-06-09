# Architecture Review Board — ARB Validator & IaC Generator

<p align="center">
  <img src="https://img.shields.io/badge/Microsoft-Foundry_v2-0078D4?style=for-the-badge&logo=microsoft&logoColor=white" alt="Foundry v2" />
  <img src="https://img.shields.io/badge/Powered_by-Azure_AI_Search-5C2D91?style=for-the-badge&logo=microsoftazure&logoColor=white" alt="Azure AI Search" />
  <img src="https://img.shields.io/badge/Auth-DefaultAzureCredential-2F855A?style=for-the-badge&logo=microsoftazure&logoColor=white" alt="DefaultAzureCredential" />
</p>

AI-powered tool that validates Architecture Solution Design (ASD) documents against organizational cloud and security policies, and generates starter Infrastructure-as-Code (Terraform) scripts from the design content.

Built on **Microsoft Foundry v2 hosted prompt agents** invoked via the **Responses API**, with retrieval driven by the Python orchestrator against an **Azure AI Search** index (hybrid + semantic ranker). **All Azure access uses identity-based auth — no API keys.** An adaptive credential picker uses `AzureCliCredential` when running locally (skipping the IMDS probe to `169.254.169.254`) and `DefaultAzureCredential` inside Azure-hosted runtimes (detected via `IDENTITY_ENDPOINT` / `MSI_ENDPOINT` / `WEBSITE_INSTANCE_ID`).

## UI Preview

<p align="center">
  <img src="docs/images/ui-screenshot.png" alt="Architecture Review Board UI" width="900" />
</p>

## Architecture

```
┌──────────────────────┐        HTTP (REST)         ┌─────────────────────────────────────┐
│   React Front-End    │  ◄──────────────────────►  │       Flask Back-End (API)           │
│   (Vite + Tailwind)  │                            │                                      │
│                      │                            │  ┌────────────────────────────────┐  │
│  • File Upload       │                            │  │ docx/pdf parsing (PyMuPDF /    │  │
│  • Validation Table  │                            │  │ python-docx)                   │  │
│  • IaC Code Display  │                            │  └────────────────────────────────┘  │
└──────────────────────┘                            │  ┌────────────────────────────────┐  │
                                                    │  │ Orchestrator (MAF) — fan-out   │  │
                                                    │  │ per (section, category)        │  │
                                                    │  └────────────────────────────────┘  │
                                                    │  ┌────────────────────────────────┐  │
                                                    │  │ search/query.py — hybrid +     │  │
                                                    │  │ semantic Azure AI Search       │  │
                                                    │  └────────────────────────────────┘  │
                                                    │  ┌────────────────────────────────┐  │
                                                    │  │ Foundry v2 hosted agents       │  │
                                                    │  │ ValidateArbAgent / IacAgent    │  │
                                                    │  │ via Responses API              │  │
                                                    │  └────────────────────────────────┘  │
                                                    └─────────────────────────────────────┘
```

### Why orchestrator-driven retrieval?

`SECTION_CATEGORIES` in `agents/validate_agent.py` routes each ASD section to one or more policy categories. The orchestrator retrieves the matching policies and injects them into the prompt as a `[Retrieved Policies]` block — the agent reasons only over what it's given. See [`prompt-contracts/AGENT-SEARCH-TOOL.md`](prompt-contracts/AGENT-SEARCH-TOOL.md) for the design rationale.

## Project structure

```
architecture-review-board/
├── .env                                   # repo-root env (FOUNDRY_LOCATION, MODEL, endpoints…)
├── README.md
├── back-end/
│   ├── app.py                             # Flask API (/validatearb, /geniac)
│   ├── requirements.txt
│   ├── agents/
│   │   ├── orchestrator.py                # ArbWorkflow (validate + iac)
│   │   ├── validate_agent.py              # Responses-API client + retrieval
│   │   ├── iac_agent.py                   # IaC generator client
│   │   ├── config.py                      # env-driven Config
│   │   ├── resilience.py                  # retry + circuit breaker
│   │   └── errors.py
│   ├── search/
│   │   ├── build_index.py                 # create/update index + ingest policies
│   │   ├── query.py                       # hybrid + semantic search wrapper
│   │   ├── categorize.py                  # header → category rules
│   │   └── index_schema.json
│   ├── infra/
│   │   ├── provision.py                   # provisions Foundry + Search in chosen region
│   │   └── create_agents.py               # creates/updates hosted prompt agents
│   ├── file_processing/
│   │   ├── parsing.py                     # docx + pdf parsing
│   │   ├── build_azure_policies.py        # builds the sample policy docx
│   │   └── data/
│   │       ├── azure_policies.docx        # ingested into arb-policies index
│   │       └── sample_asd.docx            # test input for the validator
│   └── tests/                             # pytest suite (mark integration for live tests)
├── front-end/                             # React 18 + TypeScript + Vite + Tailwind
├── prompt-contracts/                      # implementation specs (one per feature)
└── docs/
```

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.14 tested |
| Node.js | 18+ | for the front-end |
| Azure CLI | latest | `az login` before provisioning |
| Azure subscription | — | `Cognitive Services Contributor` on the RG; `Search Service Contributor` + `Search Index Data Contributor` on the AI Search service |

Supported regions for Foundry v2 hosted prompt agents (gpt-5 family): `eastus2`, `swedencentral`, `westus3`, `switzerlandnorth`, `canadaeast`, `uksouth`, and others — see [Foundry model & region support](https://learn.microsoft.com/azure/ai-foundry/agents/concepts/model-region-support). **Canada Central works at the API level** but the Foundry portal will surface an "unsupported region" warning for the new agents view; toggle to the classic portal to see them.

## One-time setup

### 1. Clone + install

```powershell
git clone https://github.com/MTCMarkFranco/architecture-review-board.git
cd architecture-review-board
cd back-end
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
cd ..\front-end
npm install
cd ..
```

### 2. Create the repo-root `.env`

```env
# Required to pick a Foundry region + model
FOUNDRY_LOCATION=canadacentral
FOUNDRY_MODEL=gpt-5.3-chat

# Subscription / tenant (az login takes care of credentials)
AZURE_SUBSCRIPTION_ID=<your-sub-id>
AZURE_TENANT_ID=<your-tenant-id>

# The rest are filled in automatically by provision.py — copy from
# back-end/.env.example after the first provision run:
# FOUNDRY_RESOURCE_GROUP=...
# FOUNDRY_ACCOUNT_NAME=...
# FOUNDRY_ENDPOINT=...
# FOUNDRY_PROJECT_NAME=...
# FOUNDRY_PROJECT_ENDPOINT=...
# FOUNDRY_MODEL_DEPLOYMENT=...
# FOUNDRY_EMBEDDINGS_DEPLOYMENT=...
# AZURE_SEARCH_ENDPOINT=...
```

`provision.py`, `app.py`, and `agents/config.py` all auto-load this file via `python-dotenv` — you do **not** need to export env vars by hand.

### 3. Provision Azure resources

```powershell
az login
cd back-end
python infra\provision.py
```

Idempotent. Creates/reuses an AI Services account, chat + embeddings deployments, a Foundry v2 project, and an Azure AI Search service. RBAC roles are assigned to your signed-in user. Outputs to `back-end/.env.example` — copy the new values into the repo-root `.env`.

### 4. Ingest the sample policies (build the search index)

```powershell
cd back-end
python -m search.build_index
```

This:
1. Creates/updates the `arb-policies` index (HNSW vector + semantic config `arb-semantic`)
2. Parses `file_processing/data/azure_policies.docx`
3. Embeds chunks via `text-embedding-3-large`
4. Uploads documents

Re-run after editing the docx or `categorize.py` rules. ⚠️ **TPM limit:** new embedding deployments default to a low tokens-per-minute quota — the script auto-retries 429s with a 60s backoff. Bump capacity in the Foundry portal if you want it faster.

### 5. Create the hosted agents

```powershell
cd back-end
python -m infra.create_agents
```

Creates `ValidateArbAgent` (prompt only — no search tool; orchestrator handles retrieval) and `IacGeneratorAgent` (code interpreter). Bumps a new version on subsequent runs.

## Daily run

**Terminal 1 — backend:**
```powershell
cd back-end
.\venv\Scripts\activate
python app.py
```
Listens on `http://127.0.0.1:5000`.

**Terminal 2 — frontend:**
```powershell
cd front-end
npm run dev
```
Opens `http://localhost:5173`. Upload an ARB doc → the UI hits `/validatearb` and `/geniac`.

## API endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/validatearb` | `multipart/form-data` field `file` (`.pdf` or `.docx`) | JSON array of finding objects |
| POST | `/geniac` | same | JSON array of Terraform script strings |

### Finding schema

```json
{
  "Type": "Violation | Deviation | Error",
  "Issue": "Brief issue title",
  "Description": "Detailed description",
  "Principles": "Policy header that was violated",
  "Mandatory": true,
  "Category": "Security and Governance"
}
```

## Validation flow

1. Front-end POSTs the doc to `/validatearb`.
2. `file_processing/parsing.py` extracts sections (Network, Storage, Security, etc.).
3. `ArbWorkflow.validate` fans out (section × category) tasks.
4. For each pair the orchestrator calls `search/query.py:search_policies` (hybrid + semantic, category-filtered) and renders the hits into a `[Retrieved Policies]` prompt block.
5. The prompt is sent to `ValidateArbAgent` through the Responses API (`project.get_openai_client(agent_name=...).responses.create(model=<deployment>, input=prompt)`).
6. JSON findings are parsed, search-failure errors are merged in, and the combined list is returned to the front-end.

## Testing

```powershell
cd back-end
pytest -q
```

Integration tests are marked `@pytest.mark.integration` and skip cleanly when `FOUNDRY_ENDPOINT` / `AZURE_SEARCH_ENDPOINT` are not set.

## Prompt contracts

Implementation specs for each feature live in [`prompt-contracts/`](prompt-contracts/). They map 1:1 to GitHub issues and branches.

## Tech stack

| Layer | Technology |
|---|---|
| Front-end | React 18, TypeScript, Vite, Tailwind CSS |
| Back-end | Python 3.11+, Flask, Flask-CORS |
| Agents | Microsoft Agent Framework, Foundry v2 hosted prompt agents, Responses API |
| Search | Azure AI Search (hybrid + semantic ranker, HNSW vectors) |
| Embeddings | `text-embedding-3-large` (Foundry deployment) |
| Auth | `DefaultAzureCredential` end-to-end — no API keys (with an adaptive credential picker that prefers `AzureCliCredential` locally to avoid the IMDS probe; falls back to `DefaultAzureCredential` inside Azure-hosted runtimes) |
| Document parsing | PyMuPDF (PDF), python-docx |
| Resilience | retry-with-backoff + circuit breaker (`agents/resilience.py`) |

## Roadmap

- [x] Migrate Semantic Kernel → MAF + Foundry v2 hosted agents
- [x] Orchestrator-driven retrieval (was: built-in AI Search agent tool)
- [x] `DefaultAzureCredential` everywhere
- [ ] Tune `WORKFLOW_TIMEOUT_SECONDS` / fan-out concurrency for full-document runs
- [ ] Promote prompt contracts into a release gate (CI check)
- [ ] Optional: agentic search wrapper for cross-cutting sections

## License

Internal use. See your organization's licensing policy.
