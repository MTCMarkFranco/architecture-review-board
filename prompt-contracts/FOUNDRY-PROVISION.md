# FOUNDRY-PROVISION — Prompt Contract

## Intent

Provide an idempotent provisioning script that guarantees an Azure AI Foundry v2 project + `gpt-5.4-pro` model deployment (with code interpreter enabled) exists in **Canada Central** under the user's current `az` subscription. The script must reuse existing resources where possible, write the resulting resource IDs to a non-secret env example file, and document its operation in the back-end README.

## Linked issue

**#13** — Provision/reuse Foundry v2 + `gpt-5.4-pro` (Canada Central, code interpreter)

## Inputs

- A logged-in `az` session (`az account show` returns a subscription).
- Currently selected subscription + tenant.
- No keys, no service principal — uses the user's identity.
- Optional env override `FOUNDRY_LOCATION` (defaults to `canadacentral`).
- Optional env override `FOUNDRY_MODEL` (defaults to `gpt-5.4-pro`).

## Outputs

- `back-end/infra/provision.py` — Python script (cross-platform).
- `back-end/.env.example` — populated with non-secret IDs:
  - `AZURE_SUBSCRIPTION_ID`
  - `AZURE_TENANT_ID`
  - `FOUNDRY_RESOURCE_GROUP`
  - `FOUNDRY_ACCOUNT_NAME`
  - `FOUNDRY_ENDPOINT`
  - `FOUNDRY_PROJECT_NAME`
  - `FOUNDRY_PROJECT_ENDPOINT`
  - `FOUNDRY_MODEL_DEPLOYMENT`
  - `FOUNDRY_EMBEDDINGS_DEPLOYMENT` (text-embedding model used by search)
  - `AZURE_SEARCH_ENDPOINT`
- Azure resources (created or reused):
  - One `Microsoft.CognitiveServices/accounts` of kind `AIServices`, SKU `S0`, location `canadacentral`.
  - One model deployment named `gpt-5.4-pro` with **code interpreter** capability enabled.
  - One Foundry v2 project under that account.
  - (Optional) One text-embedding deployment used by `SEARCH-REFACTOR`.
- Console log summarising what was reused vs created.
- A `provision.log.json` summary file (gitignored) capturing the final state.

## Edge cases & clarifications

1. **`az` not logged in / no default subscription** → exit non-zero with a one-line remediation (`az login` / `az account set -s …`); do **not** prompt interactively.
2. **`gpt-5.4-pro` model not available in `canadacentral`** → list available models via `az cognitiveservices model list -l canadacentral`, log a clear blocker, and exit non-zero without partial provisioning.
3. **Quota exceeded** when creating the deployment → catch the error, log requested-vs-available TPM, suggest opening a quota request, exit non-zero.
4. **Partial RBAC** (user has `Reader` on the RG but not `Cognitive Services Contributor`) → detect with a dry-run probe and emit a remediation note (`az role assignment create …`); do not attempt creation.
5. **Existing account exists but missing the requested deployment** → reuse the account, add only the missing deployment.
6. **Existing deployment exists but code interpreter is off** → log a warning and call `az cognitiveservices account deployment update` to enable it.
7. **Multiple Foundry accounts in Canada Central** → pick the first that already hosts `gpt-5.4-pro`; if none, pick the lexicographically first and add the deployment; log the choice.
8. **Re-run with no changes** → script reports `Reused: account=…, project=…, deployment=…` and exits 0 with no Azure writes.
9. **Network failure mid-create** → operations are retried up to 3× with exponential backoff using `azure.core` retry policy; on permanent failure, leave Azure unchanged-or-rollback-safe and exit non-zero.
10. **Secrets in output** → never write keys or connection strings into `.env.example`; only resource names, IDs, and endpoints.

## Acceptance criteria

- [ ] `back-end/infra/provision.py` exists and runs on Windows + macOS + Linux.
- [ ] Calling the script twice in a row produces zero Azure write operations on the second call.
- [ ] All edge cases above are handled with explicit error messages.
- [ ] `back-end/.env.example` is updated with the populated IDs.
- [ ] `back-end/README.md` documents prerequisites, run command, and expected exit codes.
- [ ] If `gpt-5.4-pro` is unavailable in Canada Central, the script exits with a clear blocker (no silent fallback to another model).
- [ ] No keys are persisted to disk or printed.


---

## Model fallback decision (added post-review)

**Approved by:** repo owner (ARB renewal session)

`gpt-5.4-pro` is not yet listed in Canada Central. Until it becomes available, all Foundry-bound code paths default to the env-driven `FOUNDRY_MODEL` with the fallback chain:

`gpt-5.4-pro` ➜ `gpt-5.3-chat-1` (or whatever chat-capable model is already deployed in `foundry-cc-canada`)

### Acceptance criteria for the fallback

- [ ] Provisioning script reads `FOUNDRY_MODEL` (default `gpt-5.4-pro`); if the requested model is unavailable in Canada Central, it falls back to `gpt-5.3-chat-1`, logs a clear `WARNING` line, and exits zero.
- [ ] `.env.example` documents both names in priority order.
- [ ] When `gpt-5.4-pro` becomes available the next provisioning run upgrades automatically (no code change required).
- [ ] The fallback substitution is mentioned in the PR body of every PR that touches model selection.

This addendum supersedes edge-case clause 2's "exit non-zero" rule for the specific case where the unavailable model is `gpt-5.4-pro` AND the fallback model is already deployed.
