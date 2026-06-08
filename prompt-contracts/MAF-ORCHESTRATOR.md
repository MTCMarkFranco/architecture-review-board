# MAF-ORCHESTRATOR — Prompt Contract

## Intent

Replace the Semantic Kernel + Azure OpenAI (key-auth) orchestration in `back-end/azure_local/openai_local.py` with a Microsoft Agent Framework v1.x `SequentialBuilder` pipeline that invokes two Azure AI Foundry v2 hosted prompt agents — `ValidateArbAgent` and `IacGeneratorAgent` — using `DefaultAzureCredential`. The Flask endpoint contract (`POST /validatearb`, `POST /geniac`) is preserved so the front-end remains unchanged.

## Linked issue

**#14** — Replace Semantic Kernel orchestration with MAF v1.x + Foundry v2 hosted agents

## Inputs

- All env vars produced by `FOUNDRY-PROVISION` (`FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL_DEPLOYMENT`, etc.).
- `AZURE_SEARCH_ENDPOINT` and a Foundry AI Search **connection ID** (RBAC, no keys).
- Parsed ASD dict (output of `parse_arb` / `parse_arb_docx`).

## Outputs

- `back-end/agents/__init__.py`
- `back-end/agents/config.py` — `DefaultAzureCredential` + env loader (port of reference `code/src/config.py`).
- `back-end/agents/validate_agent.py` — hosted-agent definition + invocation helper.
- `back-end/agents/iac_agent.py` — hosted-agent definition + invocation helper.
- `back-end/agents/orchestrator.py` — MAF `SequentialBuilder` chaining `parse → validate → (optional) iac`.
- `back-end/agents/resilience.py` and `back-end/agents/errors.py` — ported from reference repo.
- Rewritten `back-end/app.py` calling the new orchestrator.
- `back-end/requirements.txt` updated (add `agent-framework-foundry`, `azure-ai-projects>=2.0`, `azure-identity`, `azure-search-documents>=11.4`, `python-docx`, `pymupdf`, `pytest`; remove `semantic-kernel`, `openai`).
- `back-end/azure_local/openai_local.py` deleted.
- `scripts/create_agents.py` (or `back-end/infra/create_agents.py`) — provisions both hosted agents in Foundry v2.

## Edge cases & clarifications

1. **Missing `FOUNDRY_*` env vars** → orchestrator raises a typed `ConfigError` on import; Flask app returns HTTP 500 with a JSON body explaining missing vars (no stack trace leak).
2. **Agent not yet deployed in Foundry** → on first call, `AIProjectClient` returns 404; surface as `AgentNotFoundError` with the missing agent name and a hint to run the provisioning script.
3. **Foundry transient 5xx / rate limit** → retried by `async_retry_with_backoff` (max 3 attempts, exponential backoff). Non-transient (4xx) propagates immediately.
4. **Workflow timeout** (`WORKFLOW_TIMEOUT_SECONDS`, default 60) → orchestrator raises `WorkflowTimeoutError`; Flask returns HTTP 504.
5. **Empty / N/A section in parsed ASD** → orchestrator skips the section silently (does not call the agent with empty content). Matches prior behaviour.
6. **Agent returns malformed JSON** → orchestrator parses, falls back to a single error record `{"Type":"Error","Issue":"agent_output_unparseable",…}` rather than 500.
7. **IaC agent returns a single Python literal string instead of a JSON array** → orchestrator handles both via `json.loads` first, `ast.literal_eval` fallback (mirroring existing behaviour).
8. **Concurrent requests** → orchestrator is request-scoped; no shared mutable state between requests.
9. **Front-end backwards compatibility** → response JSON shapes for both endpoints must remain a list (`/validatearb` → list of finding objects, `/geniac` → list of IaC script strings).
10. **No keys in code or env** → `AzureKeyCredential` and `OPENAI_API_KEY` references are forbidden; CI grep can enforce.

## Acceptance criteria

- [ ] `semantic-kernel` is no longer in `requirements.txt`.
- [ ] `back-end/azure_local/openai_local.py` is deleted.
- [ ] `app.py` imports only from `back-end/agents/`.
- [ ] `agents/orchestrator.py` uses `SequentialBuilder` from `agent_framework.orchestrations`.
- [ ] Both agents are constructed with `azure.identity.DefaultAzureCredential`.
- [ ] `pytest back-end/tests/` collects without import errors.
- [ ] Endpoints `/validatearb` and `/geniac` still accept `multipart/form-data` with field `file` and return JSON.
- [ ] No `AzureKeyCredential` and no `api_key=` literal anywhere in `back-end/`.
