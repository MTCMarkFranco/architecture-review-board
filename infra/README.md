# Infrastructure (azd + Bicep)

Everything needed to stand up the Architecture Review Board solution in Azure
with a single `azd up`. The deployment provisions all infrastructure, creates
the two Microsoft Entra app registrations, wires up every environment variable,
and deploys both the Flask backend and the React SPA.

## What gets created

| Component | Resource | Notes |
|---|---|---|
| AI Foundry | `Microsoft.CognitiveServices/accounts` (kind `AIServices`) + `projects` | Hosts the chat, categorize and embedding model deployments + the Foundry v2 project |
| Search | `Microsoft.Search/searchServices` | Semantic ranker, system-assigned identity, AAD-only |
| Storage | `Microsoft.Storage/storageAccounts` + container | Policy-ingest pull pipeline; shared keys disabled |
| Key Vault | `Microsoft.KeyVault/vaults` | Stores the backend API Entra client secret (OBO) |
| Monitoring | Log Analytics + Application Insights | Backend telemetry |
| Hosting | 1 Linux App Service plan + 2 web apps | `backend` (Python 3.11 / gunicorn) and `frontend` (Node 20 / pm2 SPA) |
| Identity | Entra app registrations `<env>-arb-api` + `<env>-arb-spa` | Created by the pre/post-provision hooks |
| RBAC | Role assignments | Backend MI, Search MI, and the deploying user — all identity-based, no keys |

## Layout

```
azure.yaml                     # azd service + hooks definition
infra/
  main.bicep                   # subscription-scope entry point (creates the RG)
  main.parameters.json         # azd parameter bindings
  abbreviations.json           # resource name prefixes
  bicepconfig.json
  modules/
    monitoring.bicep
    storage.bicep
    search.bicep
    ai-foundry.bicep
    keyvault.bicep
    appservice.bicep
    rbac.bicep
  hooks/
    preprovision.ps1 / .sh     # create Entra app registrations + API secret
    postprovision.ps1 / .sh    # finalize SPA redirect URIs + VITE_* build vars
```

## Deploy

```bash
azd auth login
azd env new arb-exp          # pick an environment name
azd up                       # provision everything + deploy both apps
```

`azd up` will prompt for the target **subscription** and **location**. The
Entra app registrations are created automatically by the hooks (they require an
`az`/`azd` login with permission to create app registrations).

### Tunable parameters

Override before `azd up` with `azd env set <NAME> <value>`:

| azd env var | Default | Purpose |
|---|---|---|
| `AZURE_AI_LOCATION` | `canadacentral` | Region for the AI Foundry account + models |
| `APP_SERVICE_PLAN_SKU` | `B2` | App Service plan SKU (use `P1v3` for production / `alwaysOn`) |
| `CHAT_MODEL_NAME` / `_VERSION` | `gpt-4o` / `2024-11-20` | Validate + IaC agent model |
| `CATEGORIZE_MODEL_NAME` / `_VERSION` | `gpt-4o-mini` / `2024-07-18` | Chunk categorizer skill |
| `EMBEDDING_MODEL_NAME` / `_VERSION` | `text-embedding-3-large` / `1` | Vectorization |

> The default models are broadly available GA models. If your subscription is
> approved for the `gpt-5.x` models referenced in the app's defaults, set the
> `*_MODEL_NAME` / `*_MODEL_VERSION` vars accordingly.

## One-time data setup (after the first `azd up`)

The infrastructure is ready, but the policy index and hosted agents still need
to be populated. From an `az login` shell with the deploying identity (which the
RBAC module already entitled):

```bash
# 1. Upload the source policy document into the ingest container
az storage blob upload \
  --account-name <STORAGE_ACCOUNT_NAME> \
  --container-name arb-policies-source \
  --name azure_policies.docx \
  --file back-end/file_processing/data/azure_policies.docx \
  --auth-mode login --overwrite

# 2. Build + run the pull-mode indexer, then create the hosted agents
cd back-end
python -m search.build_indexer --run
python -m infra.create_agents

# 3. If your tenant requires admin consent for the API scope
az ad app permission admin-consent --id <ENTRA_API_CLIENT_ID>
```

All of the names above (`STORAGE_ACCOUNT_NAME`, `ENTRA_API_CLIENT_ID`, etc.) are
written to `.azure/<env>/.env` by azd and echoed by the postprovision hook.

## Notes / decisions

- **App registrations in hooks, not Bicep.** Entra app registrations cannot be
  created by ARM/Bicep, so the pre/post-provision hooks use `az ad app`. They
  are idempotent (reused by display name) and rotate the OBO secret each run.
- **No keys anywhere.** Search, Storage, Key Vault and the AI account all have
  local/key auth disabled. Access is via managed identities (apps) and the
  deploying user, granted by `modules/rbac.bicep`.
- **`alwaysOn`** is enabled only on Premium (`P*`) SKUs — Basic tiers reject it.
  Bump `APP_SERVICE_PLAN_SKU` to `P1v3` for production to keep both apps warm.
