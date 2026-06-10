# MISSING-VERIFY — Prompt Contract

## Intent

After per-chunk fan-out + `dedupe_missing_findings` (see [`MISSING-DEDUP.md`](MISSING-DEDUP.md)), prove or disprove each surviving `Missing` finding at the **document level** before reporting it to the user. The per-chunk agent has chunk-level myopia: a principle the agent flags as missing in 26 chunks may actually be addressed in 1 chunk it didn't flag — in which case we should **drop** the finding (false positive), not report it with a chunk-count suffix or a misleading "missing in N sections" framing.

For findings that **are** genuinely absent everywhere, rewrite the user-facing `Description` so it ends with the clean, trustworthy claim `"Not defined anywhere in the document."` — no chunk counts, no implementation-detail leakage.

This is the v2 of the dedupe story. v1 (#73, #74) collapsed N duplicates into one with a `"(also missing in N other chunks)"` suffix. The suffix leaks the fan-out shape, is sometimes wrong (when the principle IS addressed somewhere the chunk-agent missed), and reviewers don't think in chunks.

## Linked issue

**#77** — Verify Missing findings at document level before reporting

## Inputs

- `back-end/agents/validate_agent.py::validate_arb_chunks` — current entry point. After dedupe, has the deduped `findings` list and the full set of chunks in scope.
- `back-end/agents/categorize_chunk.py::_get_aoai_client` — already-cached AOAI client constructor used for the fast deployment (`FOUNDRY_CATEGORIZE_DEPLOYMENT`, falls back to `FOUNDRY_MODEL_DEPLOYMENT`).
- `back-end/agents/dedupe_missing_findings` — produces the input shape: one Missing finding per distinct normalized `Principles` value, optionally with `"(also missing in N other chunk[s])"` suffix in `Description`.
- Env vars `FOUNDRY_ENDPOINT`, `FOUNDRY_CATEGORIZE_DEPLOYMENT` (or `FOUNDRY_MODEL_DEPLOYMENT`).

## Outputs

- **New module** `back-end/agents/verify_missing.py`
  - `verify_missing_findings(findings, doc_text, cfg) -> list[dict]` (async)
  - Internals: `_DOC_TEXT_CHAR_BUDGET`, `_VERIFY_SYSTEM_PROMPT`, `_strip_chunk_count_suffix(description)`, `_verify_one_principle(client, deployment, principle, doc_text) -> tuple[bool, str | None]`, `_parse_verify_response(raw) -> tuple[bool | None, str | None]`.
- **Edit** `back-end/agents/validate_agent.py`
  - Assemble `doc_text` from the chunker output and pass it into a new `verify_missing_findings(...)` call run **after** `dedupe_missing_findings(...)`.
  - Helper invocation guarded by `cfg.missing_verify_enabled`.
- **Edit** `back-end/agents/config.py`
  - Add `missing_verify_enabled: bool` (default `True`) — env: `MISSING_VERIFY_ENABLED` (parsed as `"1"/"true"/"yes"` truthy, anything else falsy).
  - Add `missing_verify_max: int` (default `10`) — env: `MISSING_VERIFY_MAX`. Cap on the number of principles we issue verify calls for per validate run.
- **Edit** `back-end/.env.example` — document both new vars.
- **Edit** `back-end/README.md` — document both new vars in the env-vars table.
- **New tests** `back-end/tests/test_verify_missing.py` covering every edge case below.
- **New contract** `prompt-contracts/MISSING-VERIFY.md` (this file) + README index row.

## Behavior specification

`verify_missing_findings(findings, doc_text, cfg)`:

1. If `cfg.missing_verify_enabled is False` → return `findings` unchanged.
2. Partition `findings` into `(missing_with_principle, other)` where `missing_with_principle` are entries with `Type == "Missing"` and a non-empty normalized `Principles`. Empty-principle Missing findings stay in `other` (they were not merged by dedupe; we cannot verify what is not named).
3. Take the **distinct principles** from `missing_with_principle` in first-appearance order. Cap the list at `cfg.missing_verify_max`. Log a warning if truncated.
4. For each principle in the capped list, in parallel via `asyncio.gather`, call `_verify_one_principle(client, deployment, principle, doc_text)`. Each call:
   - Sends `_VERIFY_SYSTEM_PROMPT` + a user message containing the principle and the (truncated, see below) document text.
   - Asks the model to return a single JSON object: `{"present": true|false, "quote": "<short evidence quote>"}` — `quote` is required only when `present` is `true`.
   - Parses leniently via `_parse_verify_response` (cleanup mirrors `_strip_to_category`). Returns `(present_bool, quote_or_none)`.
   - On any exception or unparseable response → return `(None, None)` so the caller knows to leave the finding unchanged. **Never raise.**
5. Build the output list, preserving the original order of `findings`:
   - For findings in `other`: pass through unchanged.
   - For findings in `missing_with_principle`:
     - If their principle was **beyond the cap** → pass through unchanged (with the count suffix dedupe put on).
     - If `_verify_one_principle` returned `(True, _)` → **drop** the finding from output. Log INFO with the principle and the truncated quote.
     - If `_verify_one_principle` returned `(False, _)` → keep the finding, rewrite `Description`:
       - `cleaned = _strip_chunk_count_suffix(original_description)`
       - `new_desc = cleaned + " Not defined anywhere in the document."` (single space; the leading text already ends with `.` from the agent).
     - If `_verify_one_principle` returned `(None, None)` (call failed / unparseable) → pass through unchanged. Log WARNING.
6. Return the rebuilt list. **Do not mutate the input list.**

`_strip_chunk_count_suffix(description)`:

- Removes a trailing `"(also missing in N other chunk[s])"` token (regex `\s*\(also missing in \d+ other chunks?\)\s*$`) from the string and re-strips trailing whitespace. Idempotent; no-op if absent.

`_VERIFY_SYSTEM_PROMPT` (frozen for byte-stability):

```
You are an Azure architecture-review verifier. The user will give you a POLICY
PRINCIPLE name and the full text of an Architecture Design Document (ASD).
Your job is to decide whether the document, anywhere in its body, addresses
that principle — even briefly, even imperfectly. Boilerplate mentions in a
table of contents, glossary, or sign-off block do NOT count as addressing
the principle.

Return ONLY a single JSON object on one line, no prose, no code fence:
{"present": true, "quote": "<one short verbatim phrase from the document>"}
or
{"present": false, "quote": ""}

If the document mentions the principle by name but does not state any
substantive content about it, set present=false.
```

User message template:

```
PRINCIPLE:
<principle>

DOCUMENT:
<doc_text up to _DOC_TEXT_CHAR_BUDGET chars>
```

`_DOC_TEXT_CHAR_BUDGET = 120_000` characters. If `doc_text` exceeds this, truncate from the end with a `"...[truncated]"` marker and log INFO. (At ~4 chars/token this is ~30k tokens, well inside the context window of `gpt-5.4-mini` and similar fast models; the right tail of a long ASD is usually appendices/glossary that don't address core principles.)

## Edge cases & clarifications

1. **Verify disabled.** `MISSING_VERIFY_ENABLED=false` → no doc-level calls, dedupe output passes through with the `"(also missing in N other chunks)"` suffix intact. Lets ops disable cheaply if Foundry is degraded.
2. **No Missing findings.** Function returns input unchanged in O(n) without any model calls.
3. **Empty `Principles` on a Missing finding.** Not deduped by `dedupe_missing_findings`, not verified here. Passes through with whatever description it had.
4. **Cap exceeded.** If `len(distinct_principles) > cfg.missing_verify_max`, only the first N (by first-appearance order) are verified. Tail items keep their original description (count suffix preserved). A WARNING log line names the count + cap.
5. **Doc text truncation.** Doc longer than `_DOC_TEXT_CHAR_BUDGET` → truncated **from the end** with a marker; INFO log. Front-matter (TOC, exec summary) and main body are preserved; appendices may be lost. Acceptable for a yes/no presence check.
6. **Model call failure for one principle.** Returns `(None, None)`, the finding passes through unchanged. Other principles continue. Whole batch never fails.
7. **Model returns malformed JSON.** Cleanup via `_parse_verify_response` tries: strip code fences, strip leading bullets/quotes, attempt `json.loads`. If still unparseable → `(None, None)`. WARNING log includes the raw response truncated to 200 chars.
8. **`present: true` but no quote.** Accept; drop the finding anyway. Quote is for log diagnostics, not for the user-facing response.
9. **`present: false` with a quote.** Accept; ignore the quote. Rewrite description.
10. **Idempotence.** Running verify twice on the same output is a no-op: the second call's input has no `"(also missing in N other chunks)"` suffix to strip, and any verified-absent finding already ends with `"Not defined anywhere in the document."` — running through `_strip_chunk_count_suffix` is a no-op, but appending the sentence again would duplicate it. So `_strip_chunk_count_suffix` + concat is **gated**: only append `"Not defined anywhere in the document."` if it isn't already a suffix.
11. **Principles with separators (e.g. `"resilience; data-protection"`).** Treated as one opaque string for the verify call. Out of scope to split. (Same note in `MISSING-DEDUP.md`.)
12. **Does not mutate input.** New `output` list of new dicts; original `findings` and their dicts are untouched.
13. **Order preservation.** Output preserves the input order. A dropped Missing finding leaves a gap that closes naturally.

## Acceptance criteria

- [ ] `back-end/agents/verify_missing.py` exists and exports `verify_missing_findings` plus helpers.
- [ ] `validate_arb_chunks` calls `dedupe_missing_findings` then `verify_missing_findings(...)` (guarded by `cfg.missing_verify_enabled`), passing the assembled `doc_text` and `cfg`.
- [ ] `Config` adds `missing_verify_enabled` and `missing_verify_max` with documented env vars.
- [ ] `.env.example` + `README.md` document both new vars.
- [ ] `test_verify_missing.py` covers all 13 edge cases above and passes.
- [ ] Existing `test_validate_dedup.py` still passes (no regressions to dedupe contract).
- [ ] On `sdd-abc-sample.pdf`:
  - No surviving Missing finding's `Description` contains `"(also missing in"`.
  - At least one Missing finding is **dropped** by verification OR the run logs zero dropped findings explicitly with an INFO line.
  - All surviving Missing descriptions end with `"Not defined anywhere in the document."`.
