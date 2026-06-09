# AGENT-SEARCH-TOOL — Prompt Contract

## Intent

Replace the built-in `azure_ai_search` agent tool with **orchestrator-driven retrieval** for `ValidateArbAgent`. Per-section, the orchestrator calls `search/query.py:search_policies` directly with the section's category filter, then injects the retrieved policies into the agent prompt as a `[Retrieved Policies]` block. The agent reasons over the supplied policies and never calls a search tool itself.

This is the more accurate, more auditable, and more flexible pattern for ARB's category-routed retrieval. The agent stops being responsible for retrieval; it focuses on validation logic. Future retrieval experiments (rerank, HyDE, query expansion, multi-index blend) become pure Python changes — no agent redeploy.

## Linked issue

**#53** — Custom search tool: orchestrator-driven retrieval for ValidateArbAgent

## Inputs

- Existing `back-end/search/query.py:search_policies` (hybrid + semantic, RBAC).
- Existing `SECTION_CATEGORIES` map in `back-end/agents/validate_agent.py`.
- `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX` env (already wired).
- `DefaultAzureCredential` (no API keys).

## Outputs

- `back-end/agents/validate_agent.py`
  - New private helper `_retrieve_for_section(section_text, category, top_k=8) -> list[dict]`.
  - Prompt assembly updated to include a `[Retrieved Policies]` block with `header`, `category`, `@rerank` score, and a truncated `content` snippet for each hit.
  - Agent SYSTEM_PROMPT updated to instruct the agent to reason ONLY over the supplied policies (no tool-calling).
- `back-end/infra/create_agents.py`
  - Drop the `azure_ai_search` tool from `ValidateArbAgent` definition (IacGeneratorAgent unchanged — it still needs code_interpreter).
  - `FOUNDRY_SEARCH_CONNECTION_ID` becomes optional for the validate agent path.
- `back-end/tests/test_validate_search.py` (new) — unit tests with `search_policies` monkeypatched.

## Edge cases & clarifications

1. **Search returns zero results** → still call the agent; inject `[Retrieved Policies]\n(none)` so the agent can return `[]` or note the gap deterministically.
2. **`search_policies` raises** → record a single `agent_call_failed` style finding (`Type=Error`, `Issue=search_failed`) for that (section, category); do NOT call the agent for that pair.
3. **Section text is large (>16 KB)** → truncate the **query** input to `search_policies` at 16 KB; do not truncate what's sent to the agent.
4. **Long policy content** → truncate each policy's `content` to 4 KB when embedding in the prompt; full content stays in the search response if the caller wants to inspect.
5. **Category missing from `SECTION_CATEGORIES`** → use `["general"]` (current behavior); pass `category="general"` to `search_policies`. The index has a `general` bucket per `categorize.py`.
6. **Concurrent calls** → `search_policies` builds a fresh `SearchClient` per call (current code) — safe under asyncio fan-out; no shared mutable state introduced here.
7. **Retrieval latency** → call `search_policies` in a thread executor inside the existing `_call_agent` async path so the asyncio fan-out is not blocked.

## Acceptance criteria

- [ ] `validate_arb_sections` fans out (section, category) pairs as today; each task now retrieves policies BEFORE calling the agent.
- [ ] Prompt sent to the agent contains a `[Retrieved Policies]` block with at least `header` and a content snippet for each hit (or `(none)`).
- [ ] `SYSTEM_PROMPT` no longer references "policies you retrieve from the AI Search knowledge base"; instead instructs the agent to use the supplied `[Retrieved Policies]` block exclusively.
- [ ] `create_agents.py` ValidateArbAgent definition has **no** `azure_ai_search` tool.
- [ ] When `search_policies` raises, an `Error/search_failed` finding is recorded and the agent is NOT invoked for that pair.
- [ ] All existing tests continue to pass.
- [ ] At least one new test asserts the prompt to `_call_agent` contains the retrieved header.
- [ ] At least one new test asserts the search-exception path produces an `Error/search_failed` finding.
