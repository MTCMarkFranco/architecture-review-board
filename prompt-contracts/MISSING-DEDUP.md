# MISSING-DEDUP — Prompt Contract

## Intent

Collapse duplicate `Missing`-type findings emitted by `validate_arb_chunks` so that a single required policy topic absent from the uploaded ASD produces **exactly one** finding in the response, regardless of how many chunks the document was split into.

Today the chunked validate flow fans out per chunk and concatenates findings (`back-end/agents/validate_agent.py::validate_arb_chunks`). When the same gap (e.g. "RTO/RPO not specified") applies to multiple chunks of resilience-adjacent prose, the agent emits the same `Missing` finding from each chunk and the API returns 6+ copies of the same gap. The UI surfaces them all and the reviewer sees noise.

The fix is a **post-aggregation dedupe step** keyed on `Principles` for `Type == "Missing"` only. Other types pass through unchanged because Violation/Deviation/Suggestion findings are tied to specific chunk content and merging them would lose signal.

## Linked issue

**#73** — Dedupe repeated 'Missing' findings by Principle across chunks

## Inputs

- `back-end/agents/validate_agent.py` — current chunked validate flow. `validate_arb_chunks` builds a flat `findings: list[dict]` from per-chunk task results and returns it directly.
- Finding schema (set by `SYSTEM_PROMPT` in the same file):
  ```
  {"Type": "Violation|Deviation|Suggestion|Missing",
   "Issue": "<short title>",
   "Description": "<detail>",
   "Principles": "<policy header>",
   "Mandatory": <bool>,
   "Category": "<policy category>"}
  ```
- `back-end/tests/test_chunk_validate.py` — existing tests for the chunked flow; new tests live alongside.
- `back-end/file_processing/sdd-abc-sample.pdf` + its diagnostic — real-world doc used for the acceptance run.

## Outputs

- `back-end/agents/validate_agent.py`
  - New module-level helper `dedupe_missing_findings(findings: list[dict]) -> list[dict]`.
  - `validate_arb_chunks` calls it once immediately before `return findings` (and only there — `validate_arb_sections` is legacy/section-based and is **not** changed).
- `back-end/tests/test_validate_dedup.py` — new unit-test file covering the helper directly (table-driven), plus one integration-style test that drives `validate_arb_chunks` with mocked `_validate_single_chunk` and asserts dedupe occurred.
- `prompt-contracts/MISSING-DEDUP.md` — this file.
- `prompt-contracts/README.md` — add row to the "Contracts in this set" table.

No env vars, no Foundry agent changes, no skillset changes, no schema changes.

## Behavior specification

`dedupe_missing_findings(findings)`:

1. Iterate `findings` in order. Maintain `output: list[dict]` and `index_by_key: dict[str, int]` mapping the dedupe key to its position in `output`.
2. For each finding `f`:
   - If `f.get("Type") != "Missing"` → append `f` to `output` unchanged. Continue.
   - Compute `key = _missing_key(f)` (see below). If `key` is `None`, append `f` unchanged (do not merge).
   - If `key not in index_by_key`: append `f` to `output`, record `index_by_key[key] = len(output) - 1`.
   - Else: increment the **duplicate counter** on the surviving finding (stored on a private `_dup_count` attr during the pass).
3. After the pass, for each surviving Missing finding with `_dup_count > 0`, rewrite its `Description`:
   - `new_desc = f"{original_desc} (also missing in {dup_count} other chunk{'s' if dup_count != 1 else ''})"`
   - Pop the `_dup_count` key before returning so the public schema is unchanged.
4. Return `output`. **Do not mutate the input list.**

`_missing_key(f)`:

- Normalize: `principles = (f.get("Principles") or "").strip().lower()`.
- If `principles == ""`: return `None` (do not merge empty-principles Missing findings — they may be unrelated real gaps the agent failed to attribute to a header).
- Else: return `principles`.

## Edge cases & clarifications

1. **Case / whitespace variance.** Agent output for `Principles` is normalized by `.strip().lower()` only for the dedupe key. The surviving finding keeps its original `Principles` casing — we do not rewrite it.
2. **Empty `Principles`.** Treated as `None` key → never merged. Two findings with empty `Principles` remain as two findings.
3. **`Principles` with separators.** Some agent outputs put multiple principles in one field (e.g. `"resilience; data-protection"`). For v1 we treat the entire string as one opaque key. Splitting on `;`/`,` is **out of scope** and noted as a follow-up.
4. **Mandatory flag conflict.** If two collapsed Missing findings disagree on `Mandatory`, the surviving (first) finding's value wins. Rationale: same principle → same mandatoriness; disagreement is an upstream bug, not something dedupe should resolve.
5. **Category conflict.** Same as Mandatory — first wins. Logged at DEBUG when collapsed entries' `Category` differs.
6. **Order preservation.** `output` preserves the order findings first appeared. Reviewers expect the same chunk-order narrative they get today, minus duplicates.
7. **Non-dict entries.** `validate_arb_chunks` always builds dicts; defensive check is unnecessary but a non-dict entry passes through untouched (truthy-skip on the `Type` check).
8. **Error findings.** `{"Type": "Error", ...}` synthesised on agent/search failure must not be collapsed (different chunks, different errors are independently actionable).
9. **Idempotence.** `dedupe_missing_findings(dedupe_missing_findings(x)) == dedupe_missing_findings(x)` — second pass adds no further suffix because `_dup_count` resets per call and a single Missing has nothing to merge with. Tests assert this.
10. **Pluralization.** Suffix uses `"chunk"` for 1 duplicate and `"chunks"` for >1. (Note: a "1 duplicate" case means 2 originals collapsed.)
11. **No suffix when no duplicates.** A Missing finding seen once is returned with its `Description` untouched.

## Acceptance criteria

- [ ] `dedupe_missing_findings` exists in `back-end/agents/validate_agent.py` and is invoked from `validate_arb_chunks` only.
- [ ] `validate_arb_sections` is untouched.
- [ ] `back-end/tests/test_validate_dedup.py` added, covering all 11 edge cases above; full test suite green.
- [ ] On `sdd-abc-sample.pdf`, post-dedupe response contains no two `Missing` findings sharing the same normalized `Principles`. Total Missing count is strictly lower than baseline.
- [ ] No regression in `test_chunk_validate.py` (the per-chunk integration test).
- [ ] `prompt-contracts/MISSING-DEDUP.md` added; README index row added.
- [ ] PR body links issue #73 and this contract path.
