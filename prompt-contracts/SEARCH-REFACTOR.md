# SEARCH-REFACTOR — Prompt Contract

## Intent

Replace `azure_local/search_service.py` with a Canada-Central, RBAC-authenticated Azure AI Search integration that supports **hybrid retrieval** (keyword + vector) with **semantic ranking**, and exposes a **filterable, facetable `category`** field plus a `source_doc` filter. Provide a declarative `index_schema.json` and a build/ingest script that derives `category` from policy headers.

## Linked issue

**#15** — Refactor Azure AI Search → hybrid + semantic + filterable `category`

## Inputs

- `AZURE_SEARCH_ENDPOINT` (e.g. `https://ai-search-hub-canada.search.windows.net`).
- `FOUNDRY_EMBEDDINGS_DEPLOYMENT` (text-embedding model on Foundry v2 in Canada Central).
- `back-end/file_processing/data/policies.json` (existing) **and** the docx produced by `POLICIES-DOC` (#17).
- `DefaultAzureCredential` (no `AZURE_SEARCH_API_KEY`).

## Outputs

- `back-end/search/__init__.py`
- `back-end/search/index_schema.json` — declarative index definition.
- `back-end/search/build_index.py` — creates/updates index and ingests documents.
- `back-end/search/query.py` — wraps hybrid+semantic search with optional `category` and `source_doc` filters; used by `agents/validate_agent.py`.
- An Azure AI Search index named `arb-policies` (configurable) with:
  - `id` (key, String)
  - `header` (Searchable, String)
  - `content` (Searchable, String, English analyzer)
  - `contentVector` (Collection(Edm.Single), HNSW vector profile)
  - `category` (Searchable, **filterable**, **facetable**, String)
  - `source_doc` (Searchable, filterable, String)
  - Semantic configuration `arb-semantic` over `header` (title) and `content` (body).

## Edge cases & clarifications

1. **Existing `policy_index` (legacy) present** → leave alone; create a separate `arb-policies` index so rollback is trivial.
2. **Header lacks a recognised keyword** → categorise as `general`; log at WARNING.
3. **Empty content** → skip the document, log at INFO.
4. **Embedding deployment unavailable** → script logs a clear remediation and exits non-zero before any uploads.
5. **Vector dimensionality mismatch** → schema dimensions read from a constant (`EMBEDDING_DIMENSIONS = 1536`); if the embedding model returns a different size, raise `IndexSchemaMismatch`.
6. **Large content (>32 KB)** → split into chunks of ≤4 KB before embedding; document the chunking strategy in `build_index.py`.
7. **Duplicate `id`** after slugifying headers → append a numeric suffix (`-2`, `-3`).
8. **Partial ingest failure** (some documents rejected) → script reports per-document status, exits non-zero if **any** doc failed.
9. **RBAC missing `Search Index Data Contributor`** → catch and emit remediation (`az role assignment create --role "Search Index Data Contributor" ...`).
10. **Query path** must default to `query_type=vector_semantic_hybrid` when both vector + semantic config are configured.

### Category derivation rules

| Header contains (case-insensitive) | Category |
|---|---|
| `security`, `governance`, `compliance`, `policy` | `Security and Governance` |
| `network`, `firewall`, `segmentation` | `Network` |
| `storage`, `data`, `database`, `backup` | `Storage and Data` |
| `identity`, `access`, `iam`, `rbac` | `Identity and Access` |
| `cost`, `tagging`, `billing` | `Cost Optimization` |
| `observ`, `monitor`, `logging` | `Operational Excellence` |
| `performance`, `efficien`, `scale` | `Performance and Efficiency` |
| `reliability`, `disaster`, `continuity` | `Reliability` |
| `devops`, `ci`, `cd`, `iac` | `Operational Excellence` |
| `ai`, `agent`, `model`, `ml` | `AI Workloads` |
| (none match) | `general` |

## Acceptance criteria

- [ ] `back-end/search/index_schema.json` exists and matches the field list above.
- [ ] `build_index.py` is idempotent (`create_or_update_index` semantics).
- [ ] Hybrid + semantic config returns ≥1 result for a known seed query (`"identity"`, `filter=category eq 'Identity and Access'`).
- [ ] No `AzureKeyCredential` in `back-end/search/`.
- [ ] Validate agent's search calls include `vector_queries`, `semantic_configuration_name`, and a `filter` parameter.
