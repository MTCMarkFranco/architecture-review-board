# TEST-INGEST — Prompt Contract

## Intent

Provide a pytest that exercises the end-to-end policy ingestion pipeline against a real Azure AI Search service in Canada Central, using a uniquely-named throwaway index that is created and torn down per run. Skip cleanly when Azure env vars are absent.

## Linked issue

**#18** — Test suite — policy ingestion + categorization + index build

## Inputs

- `AZURE_SEARCH_ENDPOINT`, `FOUNDRY_EMBEDDINGS_DEPLOYMENT` env vars (for live runs).
- `DefaultAzureCredential` providing `Search Service Contributor` + `Search Index Data Contributor`.
- The generated `azure_policies.docx`.

## Outputs

- `back-end/pytest.ini` — markers (`@pytest.mark.live_azure`) and config.
- `back-end/tests/conftest.py` — fixtures (`live_azure`, `test_index_name`).
- `back-end/tests/test_policy_ingest.py` — the test module.

## Test cases

1. `test_build_docx_produces_expected_sections` — runs `build_azure_policies.py`, parses output, asserts ≥15 sections and exact headers.
2. `test_category_derivation_maps_known_keywords` — pure-Python; no Azure.
3. `test_index_create_and_ingest_round_trip` (`live_azure`) —
   - Generates a unique index name `arb-test-<8-char-hex>`.
   - Runs `build_index.py` against the test index.
   - Asserts the index exists with the semantic configuration `arb-semantic`, the vector field `contentVector`, and `category` flagged filterable.
   - Asserts `client.get_document_count() > 0`.
   - Issues a hybrid+semantic query with `filter="category eq 'Identity and Access'"` and asserts ≥1 hit.
   - Tears the index down in a `finally` block.

## Edge cases & clarifications

1. **Missing env vars** → tests marked `live_azure` are skipped with `pytest.skip("AZURE_SEARCH_ENDPOINT not set")`.
2. **Insufficient RBAC** → skip with a remediation message, do not fail.
3. **Embedding throttling** → retry with exponential backoff (3 tries) before failing.
4. **Index already exists** (rare collision) → delete and re-create.
5. **Build script raised** → assert error message contains the unavailable resource so failures are diagnosable.
6. **Test interrupted (Ctrl-C)** → teardown still runs (use try/finally, not just yield).
7. **No `azure_policies.docx` present** → test calls the generator on demand.
8. **Multi-process pytest** → each worker gets its own random index name (use `os.getpid()` in suffix).
9. **CI parallelism** → all test indexes use the prefix `arb-test-` so a sweeper cron can clean up stragglers.
10. **Network down** → mark tests as `skip` after a 5-second connect-timeout probe to the search endpoint.

## Acceptance criteria

- [ ] `pytest back-end/tests/test_policy_ingest.py` passes locally when env vars are set.
- [ ] Same command **skips cleanly** (no failures) with env vars unset.
- [ ] Index is always deleted after the test (verify via `assert test_index_name not in index_client.list_index_names()` in a final assertion).
- [ ] No keys printed in test output.
