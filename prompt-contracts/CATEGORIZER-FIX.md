# CATEGORIZER-FIX — Prompt Contract

## Intent

Fix the AOAI chunk categorizer so it stops collapsing every real-document chunk to `general`. The current `CATEGORIZE_SYSTEM_PROMPT` (in `back-end/agents/categories.py`) gives the model only category *names* — no definitions, no examples, no anti-pattern guidance — and the model takes the safe default. On `sdd-abc-sample.pdf` (43 chunks) categorization assigned `general` to **43/43**, making category-filtered retrieval a no-op.

This contract redesigns the prompt to:

1. Make every non-`general` category **operationally distinguishable** with a one-sentence definition and 2–3 keyword anchors each.
2. Force the model to pick the **most specific** category and treat `general` as a last resort (not a safe default).
3. Cover edge cases the current prompt is silent about: very short chunks, headers/TOC fragments, multi-topic chunks, ASD scaffolding text, and prose that mentions Azure services *in passing* but is actually about something else.
4. Add light few-shot anchoring with 1 example per specific category, drawn from the existing policy corpus where possible (deterministic, byte-stable).
5. Be **byte-stable across runs** (sorted by enum declaration order) so the indexer skillset hash does not churn.

The change is **prompt-only at first** (no enum changes, no re-ingest required) so it can ship behind PR #71 in isolation and be measured against the diagnostic on `sdd-abc-sample.pdf` before any downstream work.

## Linked issue

**#71** — Categorizer returns 'general' for 100% of real-doc chunks

## Inputs

- `back-end/agents/categories.py` — current `PolicyCategory` enum + `CATEGORIZE_SYSTEM_PROMPT` + `categories_for_prompt()`.
- `back-end/agents/categorize_chunk.py` — validate-time AOAI call (`max_completion_tokens=24`, default temperature, gpt-5 family).
- `back-end/search/skillset_definition.json` — pull-mode indexer skillset that uses the **same** `CATEGORIZE_SYSTEM_PROMPT` for ingest-time labeling. The two MUST stay in lockstep or the category-filter falls apart.
- `back-end/scripts/diagnose_chunks.py` — the per-chunk diagnostic used to measure category distribution.
- `back-end/file_processing/sdd-abc-sample.pdf` — the real-world test document (43 chunks).
- `back-end/file_processing/sdd-abc-sample.pdf.diagnostic.json` — baseline diagnostic showing `general: 43/43`.
- `back-end/file_processing/data/policies.json` — source policy corpus the few-shot examples must come from.

## Outputs

- `back-end/agents/categories.py`
  - Rewritten `CATEGORIZE_SYSTEM_PROMPT` with: definitions table, anti-pattern rules, few-shot block, last-resort framing for `general`.
  - New `CATEGORY_DEFINITIONS: Mapping[PolicyCategory, str]` exported as the single source of truth for category meanings (also makes definitions testable and grep-able).
  - New `CATEGORY_FEW_SHOT: list[tuple[str, PolicyCategory]]` — frozen, ordered list of (snippet, expected category) examples used to render the few-shot block.
  - `categories_for_prompt()` updated to render the **definitions** alongside names; signature unchanged.
- `back-end/agents/categorize_chunk.py`
  - `max_completion_tokens` bumped from 24 → 64 (definitions in the prompt may push the model toward slightly longer pre-token reasoning under reasoning models; output is still one category name).
  - Tighter post-parse: if the model echoes a definition line or quotes the name, strip and retry-parse before falling back to `GENERAL`.
- `back-end/tests/test_categories.py`
  - New test: every `CATEGORY_FEW_SHOT` (snippet, expected) pair round-trips through `parse_category` correctly.
  - New test: `CATEGORY_DEFINITIONS` covers every `PolicyCategory` member exactly once.
  - New test: `CATEGORIZE_SYSTEM_PROMPT` is byte-stable across two calls (no time/random injection).
- **Re-run baseline** — diagnostic on `sdd-abc-sample.pdf` re-recorded into `back-end/file_processing/sdd-abc-sample.pdf.diagnostic.json` to demonstrate the lift.

> Note: skillset JSON does NOT need a code change — it embeds `CATEGORIZE_SYSTEM_PROMPT` at deploy time. Acceptance criteria below requires a **re-deploy** of the skillset so ingest-time labels match validate-time labels.

## Edge cases & clarifications

1. **Very short chunks (< 200 chars)** — explicit rule: if the snippet is only a header, TOC entry, page number, or single-sentence fragment with no substantive Azure-policy content, return `general`. This is a legitimate use of `general`; it is the only one.
2. **Multi-topic chunks** — explicit rule: pick the category that describes the chunk's **dominant** topic (most sentences / most specific guidance). Ties broken by enum declaration order. Never invent a compound category.
3. **Passing mention of Azure services** — anti-pattern example in the prompt: "we host on AKS" inside a section about cost forecasting is `Cost Optimization`, not `Operational Excellence`. The chunk's *purpose* wins, not its noun count.
4. **ASD scaffolding text** (assumptions, glossaries, sign-off blocks) — return `general`. These chunks legitimately have no policy intent.
5. **AI-workload boundary** — `AI Workloads` wins over `Storage and Data` / `Performance and Efficiency` / `Operational Excellence` when the chunk is specifically about model deployment, prompt engineering, RAG, agent orchestration, GPU sizing for inference, or AI safety/grounding. Without an AI-specific signal, prefer the non-AI category.
6. **`Security and Governance` vs `Identity and Access`** — `Identity and Access` is narrower (auth, RBAC, managed identities, service principals, conditional access). `Security and Governance` covers everything else security-shaped (encryption, key management, secrets, compliance frameworks, Defender, policy-as-code, audit). When both apply, prefer `Identity and Access` if the chunk is *primarily* about who can do what; otherwise `Security and Governance`.
7. **`Operational Excellence` vs `Reliability`** — `Reliability` covers SLAs, availability targets, RPO/RTO, DR, multi-region, failover, chaos. `Operational Excellence` covers everything else operational (DevOps pipelines, observability, runbooks, deployment hygiene, change management). RPO/RTO/DR signals route to `Reliability` even if the chunk is in an "Operations" section.
8. **`Network` vs `Security and Governance`** — `Network` covers VNet design, peering, subnets, NSG topology, DNS, ExpressRoute, Front Door routing. Firewall **rules** and WAF policy intent route to `Security and Governance` unless the chunk is primarily about topology/routing.
9. **Model returns a definition line, quoted name, JSON, or trailing punctuation** — `categorize_chunk._parse` (new helper) strips a leading bullet, surrounding quotes, trailing punctuation, and any text after the first newline before calling `parse_category`. Only fall back to `GENERAL` after all stripping attempts fail.
10. **Token budget** — keep the full prompt (definitions + few-shot) under **1,800 input tokens** so we stay well inside the indexer skillset call budget and don't slow the per-chunk validate path materially. If we exceed it, drop one example per category (never drop definitions).
11. **Few-shot content sensitivity** — examples MUST be drawn verbatim from the public `policies.json` corpus or be obviously generic (no customer data, no internal-only language). They are baked into the indexer skillset definition and visible to anyone with read access to the deployed Search resource.
12. **Skillset deploy lockstep** — bumping the prompt requires a `python -m search.build_indexer --redeploy-skillset` run; the contract's acceptance criteria includes verifying ingest-time and validate-time labels for the same content agree on a 5-chunk spot check.
13. **Backout** — keep the previous prompt string as `CATEGORIZE_SYSTEM_PROMPT_V1` (commented or behind an env flag) for one release so we can flip back without a revert if the new prompt regresses on policy ingestion.

## Acceptance criteria

- [ ] `CATEGORY_DEFINITIONS` mapping exists, covers every `PolicyCategory` member, and is referenced by `categories_for_prompt()`.
- [ ] `CATEGORY_FEW_SHOT` list exists with **at least one** example per non-`general` category (9 categories → ≥9 examples).
- [ ] Every few-shot example's expected label round-trips through `parse_category`.
- [ ] `CATEGORIZE_SYSTEM_PROMPT` renders deterministically — two back-to-back imports produce byte-identical strings (new test).
- [ ] Rendered prompt token count (using `tiktoken` cl100k_base) is **< 1,800 tokens** (new test or doc-asserted in the contract; pick one).
- [ ] `categorize_chunk._parse` (or equivalent) strips bullets, quotes, JSON wrappers, trailing punctuation, and trailing newline text before parse; covered by unit tests with at least 5 malformed-response fixtures.
- [ ] `max_completion_tokens` raised from 24 → 64.
- [ ] Existing `tests/test_categories.py` continues to pass.
- [ ] `python -m search.build_indexer --redeploy-skillset` runs cleanly against the dev Search resource (manual verification noted in PR description).
- [ ] **Diagnostic re-run** on `sdd-abc-sample.pdf` after the change shows:
  - `general` rate **< 25 %** of chunks (≤ 10 of 43).
  - At least **4** distinct non-`general` categories represented across the 43 chunks.
  - Total chunks processed unchanged (43).
  - Updated `sdd-abc-sample.pdf.diagnostic.json` committed under `back-end/eval/baselines/` (new folder) — NOT alongside the source PDF.
- [ ] Ingest-time vs validate-time label agreement spot-check: pick 5 distinct policy snippets, run both code paths, assert identical labels. Result noted in PR description.
- [ ] PR description includes a before/after category histogram table.
