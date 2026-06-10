"""Canonical policy taxonomy for ARB Bot.

Single source of truth for the cross-cutting concerns this codebase reasons about.
Imported by:

* :mod:`agents.validate_agent` — maps each ASD section to its applicable category
  filter for retrieval (see :data:`ASD_SECTION_CATEGORIES`).
* :mod:`agents.iac_agent` — picks the ASD sections that drive IaC generation
  (see :data:`IAC_SECTIONS`).
* The AOAI categorize skill inside the pull-mode search indexer skillset —
  rendered via :func:`categories_for_prompt` so the LLM gets the exact list of
  allowed values, byte-stable across runs.
* :mod:`search.categorize` — legacy keyword fallback now delegates here and is
  only used when the AOAI skill output is missing.

Why an enum + frozen string mapping (rather than just strings):

* Catches typos at import time.
* Lets us add a coverage test that asserts every value the AOAI skill emits
  parses into :class:`PolicyCategory`.
* Makes "what is the canonical list?" a single grep.

Naming convention — values are *Title Case* strings. They are also what gets
written into the Search index ``category`` field, so renaming a value is a
breaking change requiring a re-ingest.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class PolicyCategory(str, Enum):
    """Cross-cutting concern categories used everywhere in the codebase.

    Values are the exact strings stored in the search index ``category`` field
    AND the exact strings the AOAI categorize skill must return. Adding a new
    value here requires:

    1. Re-ingesting the policy corpus so chunks get the new label.
    2. Updating any ``ASD_SECTION_CATEGORIES`` row that should now route to it.
    """

    IDENTITY_AND_ACCESS = "Identity and Access"
    NETWORK = "Network"
    STORAGE_AND_DATA = "Storage and Data"
    COST_OPTIMIZATION = "Cost Optimization"
    OPERATIONAL_EXCELLENCE = "Operational Excellence"
    PERFORMANCE_AND_EFFICIENCY = "Performance and Efficiency"
    RELIABILITY = "Reliability"
    SECURITY_AND_GOVERNANCE = "Security and Governance"
    AI_WORKLOADS = "AI Workloads"
    # GENERAL is a *real* overarching catch-all (per design decision on #61) —
    # AOAI is allowed to return it when a chunk is genuinely cross-cutting and
    # does not fit a more specific bucket. It is NOT just an error fallback.
    GENERAL = "general"

    @classmethod
    def values(cls) -> list[str]:
        """All canonical category strings, in declaration order."""
        return [c.value for c in cls]

    @classmethod
    def specific(cls) -> list["PolicyCategory"]:
        """All categories except the cross-cutting :attr:`GENERAL` bucket."""
        return [c for c in cls if c is not cls.GENERAL]


# ---------------------------------------------------------------------------
# ASD section → applicable categories
#
# Used by validate_agent.validate_arb_sections() to scope policy retrieval per
# section. Keys are the exact section names emitted by file_processing/parsing.py.
# Values are *non-empty* lists of PolicyCategory members.
#
# A section may map to multiple categories; each (section, category) pair fans
# out into its own retrieval + agent call.
# ---------------------------------------------------------------------------

ASD_SECTION_CATEGORIES: Mapping[str, list[PolicyCategory]] = {
    "Introduction":                            [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Key Functionalities/Capabilities":        [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Assumptions/Constraints/Recommendations": [PolicyCategory.RELIABILITY],
    "User/Usage Requirements":                 [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Interface Requirements":                  [PolicyCategory.SECURITY_AND_GOVERNANCE],
    "Security Requirements":                   [PolicyCategory.SECURITY_AND_GOVERNANCE],
    "Network Requirements":                    [PolicyCategory.NETWORK],
    "Software Requirements":                   [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Performance Requirements":                [PolicyCategory.PERFORMANCE_AND_EFFICIENCY],
    "Supportability Requirements":             [PolicyCategory.OPERATIONAL_EXCELLENCE],
    "Storage Requirements":                    [PolicyCategory.STORAGE_AND_DATA, PolicyCategory.COST_OPTIMIZATION],
    "Database Requirements":                   [PolicyCategory.STORAGE_AND_DATA],
    "Disaster Recovery Requirements":          [PolicyCategory.RELIABILITY],
    "Compliance Requirements":                 [PolicyCategory.SECURITY_AND_GOVERNANCE],
    "Licensing Requirements":                  [PolicyCategory.COST_OPTIMIZATION],
    "Proposed Solution":                       [PolicyCategory.OPERATIONAL_EXCELLENCE, PolicyCategory.RELIABILITY],
    "EC2 Sizing/Specifications":               [PolicyCategory.COST_OPTIMIZATION],
    "On-Prem Servers Sizing/Specification":    [PolicyCategory.COST_OPTIMIZATION],
    "Deployment Details":                      [PolicyCategory.SECURITY_AND_GOVERNANCE],
}


# Default for ASD sections we have not explicitly mapped.
DEFAULT_SECTION_CATEGORIES: list[PolicyCategory] = [PolicyCategory.GENERAL]


# ---------------------------------------------------------------------------
# IaC-relevant ASD sections
#
# Used by iac_agent.generate_iac() to pick which sections feed the
# Terraform-generation prompt. Order matters for the rendered prompt section
# ordering, so do not alphabetise.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Prompt rendering for the AOAI categorize skill
# ---------------------------------------------------------------------------

def categories_for_prompt() -> str:
    """Format the canonical category list **with definitions** for the AOAI
    categorize skill prompt.

    Byte-stable across runs (sorted by enum declaration order, not by name)
    so the indexer's skillset hash does not unnecessarily invalidate on every
    process restart.

    Returns a markdown bullet list, one line per category, each followed by
    its one-sentence definition from :data:`CATEGORY_DEFINITIONS`. The
    cross-cutting ``general`` bucket is included because it is a real allowed
    value.
    """
    return "\n".join(
        f"- **{c.value}** — {CATEGORY_DEFINITIONS[c]}" for c in PolicyCategory
    )


def _render_few_shot() -> str:
    """Render the frozen few-shot block for the categorize prompt.

    Byte-stable; one ``Snippet:`` / ``Category:`` pair per example in
    :data:`CATEGORY_FEW_SHOT` declaration order.
    """
    parts: list[str] = []
    for snippet, expected in CATEGORY_FEW_SHOT:
        parts.append(f'Snippet: "{snippet}"\nCategory: {expected.value}')
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Category definitions — single source of truth for category MEANING.
#
# Each definition is one sentence, present-tense, in the form "covers ...".
# Keep them concrete and grep-able. These render into CATEGORIZE_SYSTEM_PROMPT
# so the AOAI categorize skill can distinguish categories on substance, not
# just on names.
#
# Changing a definition does NOT require an enum change or re-ingest of the
# policy corpus (labels are unchanged) — but it DOES require a skillset
# re-deploy so the indexer's on-the-wire prompt matches the validate-time
# prompt. See CATEGORIZER-FIX prompt contract for the lockstep rule.
# ---------------------------------------------------------------------------

CATEGORY_DEFINITIONS: Mapping[PolicyCategory, str] = {
    PolicyCategory.IDENTITY_AND_ACCESS: (
        "covers authentication, authorization, RBAC, managed identities, "
        "service principals, conditional access, and entitlement management — "
        "the who-can-do-what surface."
    ),
    PolicyCategory.NETWORK: (
        "covers VNet design, subnets, peering, NSG topology, DNS, "
        "ExpressRoute, private endpoints, Front Door routing, and "
        "egress/ingress paths. Firewall rules and WAF policy intent route "
        "to Security and Governance unless the chunk is primarily about "
        "topology or routing."
    ),
    PolicyCategory.STORAGE_AND_DATA: (
        "covers blob/file/queue/table storage, relational and NoSQL "
        "databases, data lakes, backup retention, data classification, "
        "lifecycle management, and data residency."
    ),
    PolicyCategory.COST_OPTIMIZATION: (
        "covers SKU right-sizing, reservations, autoscale-for-cost, "
        "budgets, tagging for chargeback, decommissioning idle resources, "
        "and licensing optimization."
    ),
    PolicyCategory.OPERATIONAL_EXCELLENCE: (
        "covers DevOps pipelines, deployment hygiene, observability "
        "(tracing/metrics/logging), runbooks, change management, "
        "configuration drift, and day-2 operations — but NOT availability "
        "targets (those go to Reliability)."
    ),
    PolicyCategory.PERFORMANCE_AND_EFFICIENCY: (
        "covers latency targets, throughput, autoscale-for-load, caching, "
        "CDN, query tuning, and SKU sizing-for-performance (sizing-for-cost "
        "goes to Cost Optimization)."
    ),
    PolicyCategory.RELIABILITY: (
        "covers SLAs, availability targets, RPO/RTO, disaster recovery, "
        "multi-region/multi-AZ, failover, redundancy, backup-for-recovery, "
        "and chaos engineering."
    ),
    PolicyCategory.SECURITY_AND_GOVERNANCE: (
        "covers encryption (at rest and in transit), key management, "
        "secrets, compliance frameworks, Microsoft Defender, Azure Policy, "
        "policy-as-code, audit logging, firewall and WAF rules, and "
        "vulnerability management — everything security-shaped that is NOT "
        "specifically about who can do what (that routes to Identity and Access)."
    ),
    PolicyCategory.AI_WORKLOADS: (
        "covers model deployment, prompt engineering, RAG, agent "
        "orchestration, GPU sizing for inference/training, AI safety, "
        "grounding, and responsible-AI controls. Wins over Storage and "
        "Data / Performance and Efficiency / Operational Excellence when "
        "an AI-specific signal is present."
    ),
    PolicyCategory.GENERAL: (
        "a LAST-RESORT bucket for content that is genuinely cross-cutting "
        "or has no substantive Azure policy intent (TOC entries, headers, "
        "page numbers, glossaries, sign-off blocks, single-sentence "
        "fragments under 200 characters). Do NOT use as a safe default."
    ),
}


# ---------------------------------------------------------------------------
# Few-shot anchors — one example per non-general category.
#
# Frozen, ordered list. Snippets are intentionally generic Azure-policy
# phrasing — no customer data, no internal-only language — because this list
# is baked into the indexer skillset definition and visible to anyone with
# read access to the deployed Search resource. See CATEGORIZER-FIX prompt
# contract, edge case 11.
# ---------------------------------------------------------------------------

CATEGORY_FEW_SHOT: list[tuple[str, PolicyCategory]] = [
    (
        "All human and service identities accessing Azure resources must "
        "authenticate via Entra ID; local accounts and shared keys are "
        "prohibited. RBAC role assignments are scoped to the smallest "
        "necessary resource group.",
        PolicyCategory.IDENTITY_AND_ACCESS,
    ),
    (
        "Production workloads must be deployed into hub-and-spoke VNets "
        "with private endpoints for all PaaS services. Cross-region peering "
        "uses Global VNet Peering; on-prem connectivity uses ExpressRoute.",
        PolicyCategory.NETWORK,
    ),
    (
        "Customer data is stored in Azure SQL with TDE enabled and a "
        "geo-redundant backup policy of 35 days. Blob containers default to "
        "Cool tier with lifecycle rules archiving after 90 days.",
        PolicyCategory.STORAGE_AND_DATA,
    ),
    (
        "Compute SKUs are selected from the approved reservation catalogue. "
        "Non-production environments auto-shutdown nightly and tag every "
        "resource with cost-centre and owner for chargeback.",
        PolicyCategory.COST_OPTIMIZATION,
    ),
    (
        "All deployments flow through the standard Azure DevOps pipeline "
        "with mandatory PR review, IaC validation, and post-deploy smoke "
        "tests. Observability uses Azure Monitor with structured logs to "
        "Log Analytics and traces to Application Insights.",
        PolicyCategory.OPERATIONAL_EXCELLENCE,
    ),
    (
        "API endpoints must respond within 200 ms p95 under expected load. "
        "Front Door caches static assets and App Service plans autoscale on "
        "CPU > 70%. SQL queries exceeding 500 ms are flagged for tuning.",
        PolicyCategory.PERFORMANCE_AND_EFFICIENCY,
    ),
    (
        "Tier-1 workloads target 99.95% monthly availability with RPO ≤ 15 "
        "minutes and RTO ≤ 1 hour. Active-passive failover to a paired "
        "region is tested quarterly via a documented DR runbook.",
        PolicyCategory.RELIABILITY,
    ),
    (
        "All data at rest is encrypted with customer-managed keys stored in "
        "Azure Key Vault HSM. Microsoft Defender for Cloud is enabled in "
        "all subscriptions and Azure Policy enforces required tags and "
        "deny-by-default for unapproved resource types.",
        PolicyCategory.SECURITY_AND_GOVERNANCE,
    ),
    (
        "RAG pipelines ground generated answers in indexed enterprise "
        "content and emit per-response telemetry capturing prompt, "
        "retrieved context, and model output. Inference uses dedicated GPU "
        "SKUs with content-safety filters enabled.",
        PolicyCategory.AI_WORKLOADS,
    ),
]


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

# Kept for one release as a backout path. Do NOT extend.
# See CATEGORIZER-FIX prompt contract, edge case 13.
CATEGORIZE_SYSTEM_PROMPT_V1 = (
    "You categorize a snippet of Azure policy text into EXACTLY ONE of the "
    "categories listed below. Output ONLY the category name verbatim — no prose, "
    "no quotes, no JSON. If the text is genuinely cross-cutting and does not "
    "fit any specific bucket, output \"general\". Always prefer a specific "
    "category over \"general\" when one applies.\n\n"
    "Categories:\n"
    + "\n".join(f"- {c.value}" for c in PolicyCategory)
)


CATEGORIZE_SYSTEM_PROMPT = (
    "You categorize a snippet of Azure architecture or policy text into "
    "EXACTLY ONE of the categories listed below. Output ONLY the category "
    "name verbatim (e.g. `Network` or `general`) — no prose, no quotes, no "
    "bullets, no JSON, no trailing punctuation.\n\n"
    "Decision rules (apply in order):\n"
    "1. Pick the MOST SPECIFIC category that matches the dominant topic of "
    "the snippet. Ties broken by the order categories appear below.\n"
    "2. The PURPOSE of a snippet wins over its noun count. Mentioning a "
    "service in passing does NOT route to that service category.\n"
    "3. `general` is a LAST RESORT. Use it ONLY when the snippet is under "
    "200 characters of header/TOC/glossary/sign-off text, OR is so "
    "genuinely cross-cutting that no specific category captures more than "
    "half of its content. Do NOT use `general` as a safe default.\n"
    "4. Specific tie-breakers:\n"
    "   - `Identity and Access` wins over `Security and Governance` when "
    "the snippet is primarily about who can do what.\n"
    "   - `Reliability` wins over `Operational Excellence` when "
    "SLA/RPO/RTO/DR/failover signals are present.\n"
    "   - `Network` covers topology and routing; firewall and WAF rules "
    "route to `Security and Governance`.\n"
    "   - `AI Workloads` wins over `Storage and Data` / `Performance and "
    "Efficiency` / `Operational Excellence` when the snippet is "
    "specifically about model deployment, RAG, agents, inference, GPU "
    "sizing, or AI safety.\n\n"
    "Categories:\n"
    f"{categories_for_prompt()}\n\n"
    "Examples:\n"
    f"{_render_few_shot()}\n\n"
    "Snippet: \"Table of Contents — Section 4: Network Requirements ... 12\"\n"
    "Category: general\n\n"
    "Snippet: \"We will host the front-end on App Service. Estimated "
    "monthly cost is $1,200 based on the P1v3 reservation tier we already "
    "own.\"\n"
    "Category: Cost Optimization\n\n"
    "Now categorize the user-provided snippet."
)


def parse_category(value: str | None) -> PolicyCategory:
    """Parse a raw category string (e.g. from the search index) into the enum.

    Falls back to :attr:`PolicyCategory.GENERAL` for null/unknown values so
    callers downstream of the search results never crash on stale chunks.
    """
    if not value:
        return PolicyCategory.GENERAL
    cleaned = value.strip()
    for c in PolicyCategory:
        if c.value == cleaned:
            return c
    return PolicyCategory.GENERAL
