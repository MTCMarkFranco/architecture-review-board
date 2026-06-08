# Prompt Contracts

Authoritative specification for each unit of implementation work in the **ARB Bot MAF v2 renewal**. Inspired by [`Foundy-v2-mas-workflow-sample/prompt-contracts/`](https://github.com/MTCMarkFranco/Foundy-v2-mas-workflow-sample/tree/master/prompt-contracts).

## Convention

Each contract is one Markdown file named after the work item (UPPER-KEBAB-CASE) and contains:

1. **Intent** — one paragraph stating what the implementation must achieve.
2. **Linked issue** — `#N` in `MTCMarkFranco/architecture-review-board`.
3. **Inputs** — files, env vars, Azure resources required.
4. **Outputs** — files produced, endpoints, Azure resources created/modified.
5. **Edge cases & clarifications** — concrete edge cases the implementer must handle (≥5).
6. **Acceptance criteria** — checklist mapped 1:1 to PR review.

## Contracts in this set

| Contract | Issue | Branch |
|---|---|---|
| [`FOUNDRY-PROVISION.md`](FOUNDRY-PROVISION.md) | #13 | `branch-foundry-provision-1` |
| [`MAF-ORCHESTRATOR.md`](MAF-ORCHESTRATOR.md) | #14 | `branch-maf-orchestrator-1` |
| [`SEARCH-REFACTOR.md`](SEARCH-REFACTOR.md) | #15 | `branch-search-refactor-1` |
| [`SAMPLE-ASD.md`](SAMPLE-ASD.md) | #16 | `branch-sample-asd-1` |
| [`POLICIES-DOC.md`](POLICIES-DOC.md) | #17 | `branch-policies-doc-1` |
| [`TEST-INGEST.md`](TEST-INGEST.md) | #18 | `branch-test-ingest-1` |
| [`TEST-ASD.md`](TEST-ASD.md) | #19 | `branch-test-asd-1` |

## Branching choice

Feature branches branch off `master`, **not** off `branch-prompt-contracts-1`. The contracts PR is reviewed independently; feature branches reference the contract file path in their PR body so reviewers can hop straight to the spec on the contracts PR.

If the contracts PR merges first, feature branches do not require rebase (file paths under `prompt-contracts/` do not collide with feature changes). If a feature PR merges first, that's fine — contracts remain accurate as long as their acceptance criteria match the merged code.
