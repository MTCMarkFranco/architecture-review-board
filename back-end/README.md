# ARB Bot — back-end

Flask service exposing `/validatearb` and `/geniac` to the React front-end.
Runs on **Azure AI Foundry v2** with **`gpt-5.4-pro`** in **Canada Central**,
orchestrated by **Microsoft Agent Framework (MAF) v1.x** with RBAC via
`DefaultAzureCredential`.

## Prerequisites

- Python 3.11+
- `az` CLI logged in: `az login && az account set -s <subscription-id>`
- RBAC: `Cognitive Services Contributor` on the resource group, `Search Service Contributor` + `Search Index Data Contributor` on the Azure AI Search service.

## Provision (or reuse) Azure resources

```powershell
python back-end/infra/provision.py
```

The script is idempotent. It searches for an existing `AIServices` account in
Canada Central with a `gpt-5.4-pro` deployment and reuses it. Otherwise it
creates a resource group, an `AIServices` account, a `gpt-5.4-pro` deployment
with code interpreter enabled, a Foundry v2 project, and an embeddings
deployment. Resource IDs are written to `back-end/.env.example`.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success (reused or created) |
| 2 | Blocker (model unavailable, quota, RBAC, etc.) — message printed |
| 130 | Interrupted |

Use `--dry-run` to preview without making Azure changes.

### Known blocker (as of writing)

`gpt-5.4-pro` may not yet be available in Canada Central. The script lists
available `gpt-*` models in the region and exits non-zero with remediation
guidance.

## Install + run

```powershell
cd back-end
pip install -r requirements.txt
cp .env.example .env.local  # edit values
$env:DOTENV_PATH = '.env.local'
flask --app app run --debug
```

## Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/validatearb` | `multipart/form-data` field `file` (`.pdf` or `.docx`) | `application/json` — list of finding objects |
| POST | `/geniac` | same | `application/json` — list of IaC script strings |

## Tests

```powershell
pytest back-end/tests
```

Live-Azure tests are marked `@pytest.mark.live_azure` and skip when the
relevant env vars are unset. See `prompt-contracts/TEST-INGEST.md` and
`prompt-contracts/TEST-ASD.md`.
