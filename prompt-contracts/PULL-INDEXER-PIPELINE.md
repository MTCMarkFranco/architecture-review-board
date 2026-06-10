# PULL-INDEXER-PIPELINE — Prompt Contract

## Intent

Replace the **push-mode** ingest in [`back-end/search/build_index.py`](../back-end/search/build_index.py) with an **Azure AI Search pull-mode pipeline** that does document cracking + semantic chunking + per-chunk **categorization** + embeddings server-side via a skillset, and projects one index document per chunk through an indexer.

Concurrently, introduce a **single canonical `PolicyCategory` taxonomy module** that is the source of truth for:

1. The **AOAI categorize skill prompt** inside the pull-indexer skillset (categorizes each chunk during ingest).
2. **`validate_agent.py`** — replaces the ad-hoc `SECTION_CATEGORIES` dict.
3. **`iac_agent.py`** — replaces the ad-hoc `IAC_SECTIONS` list with a category-aware grouping.
4. **`search/categorize.py`** — keyword-rule fallback is retired in favour of the AOAI skill (kept only as an offline migration helper if needed).

The result: one taxonomy, used at ingest time by AOAI, and used at query/decomposition time by both agents.

## Linked issue
**#TBD** — Pull-indexer pipeline + canonical PolicyCategory taxonomy (this contract).

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌──────────────────────────────────┐
│ Blob container  │ ──► │  Search Data    │ ──► │            Skillset              │
│ arb-policies-   │     │  Source         │     │  1. CU skill — crack + chunk     │
│ source          │     │  (Managed ID)   │     │  2. AOAI chat — categorize chunk │
│                 │     │                 │     │  3. AOAI embed — vectorize chunk │
└─────────────────┘     └─────────────────┘     └──────────────────────────────────┘
                                                                  │
                                                                  ▼
                                                ┌──────────────────────────────────┐
                                                │  Indexer + Index Projections     │
                                                │  one chunk → one index doc       │
                                                │  fields: id, header, content,    │
                                                │  contentVector, category,        │
                                                │  source_doc, chunk_index         │
                                                └──────────────────────────────────┘
                                                                  │
                                                                  ▼
                                                ┌──────────────────────────────────┐
                                                │   Existing `arb-policies` index  │
                                                └──────────────────────────────────┘

                                                Foundry resource attachment on
                                                the skillset — required billing
                                                target for CU + AOAI skills.
```

## Inputs
- Existing **`arb-policies` index** + `index_schema.json` (no schema change needed — `category` field already exists).
- Existing **Foundry account `foundry-cc-canada`** with `text-embedding-3-large` embeddings deployment and a chat deployment (e.g. `gpt-5.3-chat-1`).
- Source policy document (currently `back-end/file_processing/data/azure_policies.docx`; the new pipeline must work for any blob the user drops in the source container).
- `DefaultAzureCredential` end-to-end — no API keys (per existing tech-debt #59 + adaptive-credential perf updates).

## Outputs

### New files
| File | Purpose |
|---|---|
| `back-end/agents/categories.py` | **Canonical `PolicyCategory` enum** + `ASD_SECTION_CATEGORIES` + `IAC_SECTIONS` + `categories_for_prompt()` helper for AOAI |
| `back-end/search/skillset_definition.json` | Declarative skillset JSON (CU → categorize → embed) |
| `back-end/search/indexer_definition.json` | Declarative indexer + index projection JSON |
| `back-end/search/build_indexer.py` | Provisions data source + skillset + indexer; triggers a run; tails status; optional purge |
| `back-end/infra/provision_search_pipeline.py` | Provisions the supporting Azure resources (blob storage account + container; assigns RBAC; attaches Foundry resource to the search service for skillset billing) |

### Modified files
| File | Change |
|---|---|
| `back-end/agents/validate_agent.py` | Remove inline `SECTION_CATEGORIES` dict; import from `agents.categories`; use `PolicyCategory` enum values where strings appear today |
| `back-end/agents/iac_agent.py` | Replace `IAC_SECTIONS` list with `IAC_SECTIONS` import from `agents.categories`; optionally tag generated components with the relevant category |
| `back-end/search/categorize.py` | Mark deprecated; keep `derive_category` as a fallback that returns `PolicyCategory.GENERAL.value` when AOAI skill output is missing |
| `back-end/search/build_index.py` | Deprecate the push path; either delete or keep with a clear `--legacy-push` flag for emergency manual ingest |
| `back-end/infra/provision.py` | Call `provision_search_pipeline.py` helpers so a single `python infra/provision.py` still bootstraps everything |
| `back-end/requirements.txt` | Add `azure-search-documents>=11.6.0b1` (or current release that supports CU + AOAI skills + index projections) and `azure-storage-blob` |
| `README.md` + `back-end/README.md` | Update the **Ingest the sample policies** section: drop docx → embed flow; add blob upload → run indexer flow |

## Canonical `PolicyCategory` design

```python
# back-end/agents/categories.py

from enum import Enum

class PolicyCategory(str, Enum):
    """Canonical taxonomy of cross-cutting concerns. Single source of truth for:
      - AOAI categorize skill (assigns each chunk to one category at ingest time)
      - validate_agent (maps ASD sections → category filter for retrieval)
      - iac_agent (groups IaC components by category)
    """
    IDENTITY_AND_ACCESS         = "Identity and Access"
    NETWORK                     = "Network"
    STORAGE_AND_DATA            = "Storage and Data"
    COST_OPTIMIZATION           = "Cost Optimization"
    OPERATIONAL_EXCELLENCE      = "Operational Excellence"
    PERFORMANCE_AND_EFFICIENCY  = "Performance and Efficiency"
    RELIABILITY                 = "Reliability"
    SECURITY_AND_GOVERNANCE     = "Security and Governance"
    AI_WORKLOADS                = "AI Workloads"
    GENERAL                     = "general"  # fallback only — AOAI must pick a real category when possible

# ASD section → applicable categories (used by validate_agent for retrieval filtering)
ASD_SECTION_CATEGORIES: dict[str, list[PolicyCategory]] = {
    "Introduction":                          [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Key Functionalities/Capabilities":      [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Assumptions/Constraints/Recommendations": [PolicyCategory.RELIABILITY],
    "User/Usage Requirements":               [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Interface Requirements":                [PolicyCategory.SECURITY_AND_GOVERNANCE],
    "Security Requirements":                 [PolicyCategory.SECURITY_AND_GOVERNANCE],
    "Network Requirements":                  [PolicyCategory.NETWORK],
    "Software Requirements":                 [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Performance Requirements":              [PolicyCategory.PERFORMANCE_AND_EFFICIENCY],
    "Supportability Requirements":           [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Storage Requirements":                  [PolicyCategory.STORAGE_AND_DATA, PolicyCategory.COST_OPTIMIZATION],
    "Database Requirements":                 [PolicyCategory.STORAGE_AND_DATA],
    "Disaster Recovery Requirements":        [PolicyCategory.RELIABILITY],
    "Compliance Requirements":               [PolicyCategory.SECURITY_AND_GOVERNANCE],
    "Licensing Requirements":                [PolicyCategory.COST_OPTIMIZATION],
    "Proposed Solution":                     [PolicyCategory.OPERATIONAL_EXCELLENCE, PolicyCategory.RELIABILITY],
    "EC2 Sizing/Specifications":             [PolicyCategory.COST_OPTIMIZATION],
    "On-Prem Servers Sizing/Specification":  [PolicyCategory.COST_OPTIMIZATION],
    "Deployment Details":                    [PolicyCategory.SECURITY_AND_GOVERNANCE],
}

# Sections IaC generation cares about (subset of ASD sections)
IAC_SECTIONS: list[str] = [
    "Introduction",
    "Assumptions/Constraints/Recommendations",
    "Interface Requirements",
    "Network Requirements",
    "Software Requirements",
    "Storage Requirements",
    "Database Requirements",
    "EC2 Sizing/Specifications",
]

def categories_for_prompt() -> str:
    """Format the category list for use inside AOAI categorize skill prompts."""
    return "\n".join(f"- {c.value}" for c in PolicyCategory if c != PolicyCategory.GENERAL)
```

## Skillset shape (conceptual)

```json
{
  "name": "arb-policies-skillset",
  "skills": [
    {
      "@odata.type": "#Microsoft.Skills.Util.ContentUnderstandingSkill",
      "name": "crack-and-chunk",
      "context": "/document",
      "inputs":  [{ "name": "blob", "source": "/document/file_data" }],
      "outputs": [{ "name": "chunks", "targetName": "chunks" }]
    },
    {
      "@odata.type": "#Microsoft.Skills.Custom.ChatCompletionSkill",
      "name": "categorize",
      "context": "/document/chunks/*",
      "inputs":  [{ "name": "text", "source": "/document/chunks/*/content" }],
      "outputs": [{ "name": "category", "targetName": "category" }],
      "deploymentName": "<FOUNDRY_MODEL_DEPLOYMENT>",
      "systemMessage": "You categorize a snippet of Azure policy text into EXACTLY ONE of the categories below. Output ONLY the category name verbatim, no prose.\n\n<categories>\n{{categories_for_prompt()}}\n</categories>"
    },
    {
      "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
      "name": "embed",
      "context": "/document/chunks/*",
      "inputs":  [{ "name": "text", "source": "/document/chunks/*/content" }],
      "outputs": [{ "name": "embedding", "targetName": "contentVector" }],
      "deploymentId": "text-embedding-3-large",
      "dimensions": 1536
    }
  ],
  "indexProjections": {
    "selectors": [
      {
        "targetIndexName": "arb-policies",
        "parentKeyFieldName": "source_doc",
        "sourceContext": "/document/chunks/*",
        "mappings": [
          { "name": "header",        "source": "/document/chunks/*/header" },
          { "name": "content",       "source": "/document/chunks/*/content" },
          { "name": "category",      "source": "/document/chunks/*/category" },
          { "name": "contentVector", "source": "/document/chunks/*/contentVector" },
          { "name": "chunk_index",   "source": "/document/chunks/*/index" },
          { "name": "source_doc",    "source": "/document/metadata_storage_name" }
        ]
      }
    ],
    "parameters": { "projectionMode": "skipIndexingParentDocuments" }
  },
  "cognitiveServices": {
    "@odata.type": "#Microsoft.Azure.Search.AIServicesByIdentity",
    "subdomainUrl": "<FOUNDRY_CU_ENDPOINT>",
    "identity": null
  }
}
```

> **Implementation notes (verified against API version `2025-11-01-preview`):**
>
> - `subdomainUrl` must be the **Foundry v2** AI Services subdomain (`https://<account>.services.ai.azure.com/`), **not** the legacy `cognitiveservices.azure.com` subdomain. The latter authenticates but does not satisfy the `AIServicesByIdentity` validator.
> - For system-assigned managed identity, `identity` is the literal `null` (not a typed wrapper object). User-assigned MI uses `{ "@odata.type": "#Microsoft.Azure.Search.DataUserAssignedIdentity", "userAssignedIdentity": "<resourceId>" }`.
> - `Microsoft.Skills.Util.ContentUnderstandingSkill` requires API version **`2025-11-01-preview`** or **`2026-04-01`** or later. The skill emits Markdown chunks by default — there is no `outputFormat` property.
> - The chat-completion skill is `Microsoft.Skills.Custom.ChatCompletionSkill` (the `Microsoft.Skills.Text.AzureOpenAIChatCompletionSkill` type does not exist).
> - The search service's system-assigned managed identity must have `Cognitive Services User` on the Foundry account (for CU + chat) **and** `Storage Blob Data Reader` on the source storage account. Both are granted by `infra/provision_search_pipeline.py`.
> - The index key field (`id`) must be `searchable: true` with `analyzer: "keyword"` for index projections to bind the parent-key field. The schema must also include a filterable `Edm.String` field named `source_doc_key` (the projection's `parentKeyFieldName`).

## Edge cases & clarifications

1. **CU regional availability** — CU is not GA in every region. If Canada Central is unsupported, fall back to **Sweden Central / West US 3** for the AI Services account that hosts CU, and reference it via the skillset's `cognitiveServices` attachment. **Open question** below.
2. **Embedding skill `dimensions=1536`** must match the existing index schema. Already 1536 — don't change.
3. **Category enum drift** — `PolicyCategory` must be in sync with the AOAI prompt and the search index `category` field's allowed values. Add an integration test that asserts every value emitted by AOAI for a fixture chunk parses into `PolicyCategory`.
4. **AOAI categorize prompt drift** — emitted by `categories_for_prompt()`. A unit test must assert the rendered string is stable byte-for-byte across runs (so the indexer cache doesn't unnecessarily invalidate).
5. **Idempotent indexer reruns** — `--purge` flag on `build_indexer.py` should call the existing `purge_index_documents()` before running so blob replacements don't leave orphaned chunks.
6. **Blob → docs mapping** — index doc `id` must be deterministic: `slug(source_doc) + "-" + chunk_index`. Currently the push path computes this; the projection mapping must produce the same shape so query consumers don't break.
7. **Multiple source docs** — pipeline must support multiple blobs (e.g. azure_policies.docx + AWS_policies.docx + GCP_policies.docx). `source_doc` filter at query time already supports this in `search/query.py`.
8. **Cost** — CU is metered per page; AOAI categorize per token; embeddings per token. For one ~30-section docx this is < $1 per full re-ingest. Document the cost order-of-magnitude in `back-end/README.md`.
9. **Sensitivity / failure isolation** — if any chunk's categorize step fails, that chunk must still be indexed with `category = "general"` so search recall isn't lost. Skill output mapping should tolerate nulls.
10. **Backwards compatibility for `search/query.py`** — the query layer is unchanged: it still filters by `category` and selects the same fields. No consumer change required.
11. **Validate / IaC wiring** — must use the enum's `.value` when comparing to search results (because the search field is a string), and the enum directly in code. Lint should enforce no raw strings.
12. **Test coverage** — at minimum: enum stability test, `categories_for_prompt()` snapshot test, schema-vs-enum coverage test, integration test that runs the indexer against a fixture blob and asserts every chunk has a non-empty category.
13. **Indexer schedule** — on-demand only for v1 (no cron). Re-runs are explicit via `python -m search.build_indexer --run`. **Open question** below.
14. **Blob storage provisioning** — if no existing storage account is in scope, provision a new one in the same region as the search service. Container name `arb-policies-source`. **Open question** below.

## Open questions to confirm before implementation

1. 🌍 **Storage account region** — provision a **new** dedicated storage account for source blobs (recommended; minimal blast radius), or reuse an existing one? If new, same region as the Search service (`arb-search-cc` in canadacentral).
2. 🌍 **CU regional gap** — if Canada Central does not support Content Understanding, do you want to:
   - (a) attach a separate AI Services account in a CU-supported region (e.g. Sweden Central) just for the skillset, OR
   - (b) move the whole Foundry/search/storage stack to a CU-supported region?
3. 📅 **Indexer schedule** — on-demand only (v1, simplest)? Or periodic (e.g. hourly, daily)?
4. 🗑️ **Push-path retention** — fully delete `build_index.py` push path, or keep it behind a `--legacy-push` flag for offline emergencies?
5. 🧭 **`PolicyCategory.GENERAL` policy** — should AOAI **ever** be allowed to return `general`? Today the keyword fallback returns it when nothing matches. With AOAI, prefer forcing a real category and using `general` only when the skill literally errors out. Confirm preference.
6. 🧰 **Single PR or multi-PR rollout?** This is naturally one cohesive change, but it's big (~600+ LoC). Options:
   - (a) one branch + one PR (simplest review),
   - (b) three sequential PRs: `categories.py` template first → indexer plumbing → validate/iac wiring,
   - (c) parent EPIC issue + three child issues with one PR each.

## Acceptance criteria

- [ ] `agents/categories.py` exists with `PolicyCategory` enum, mappings, and `categories_for_prompt()`.
- [ ] `validate_agent.py` and `iac_agent.py` import from `agents.categories` — no inline taxonomy strings remain.
- [ ] `search/skillset_definition.json` + `search/indexer_definition.json` exist and validate against the Search REST API schema.
- [ ] `python -m search.build_indexer` provisions data source + skillset + indexer idempotently and (with `--run`) triggers an ingest and tails status to completion.
- [ ] `python -m infra.provision_search_pipeline` provisions the storage account, container, RBAC, and Foundry attachment idempotently.
- [ ] `infra/provision.py` chains the new step so one command still bootstraps everything end-to-end.
- [ ] After a clean `--purge` + `--run`, the `arb-policies` index contains one document per chunk, every doc has a non-empty `category` matching a `PolicyCategory` value, and `source_doc` reflects the blob name.
- [ ] `agents/validate_agent.py` integration test still passes against the new index.
- [ ] All unit tests pass; new tests cover the enum stability, the prompt snapshot, the schema-vs-enum coverage, and the projection-mapping shape.
- [ ] README updates land in the same PR so the docs match the running pipeline.
- [ ] No API keys anywhere; only `DefaultAzureCredential` (and our adaptive picker).
