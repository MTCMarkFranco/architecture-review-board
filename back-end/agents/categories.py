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
    """Format the canonical category list for the AOAI categorize skill prompt.

    Byte-stable across runs (sorted by enum declaration order, not by name)
    so the indexer's skillset hash does not unnecessarily invalidate on every
    process restart.

    Returns a markdown bullet list, one line per category. The cross-cutting
    ``general`` bucket is included because it is a real allowed value.
    """
    return "\n".join(f"- {c.value}" for c in PolicyCategory)


CATEGORIZE_SYSTEM_PROMPT = (
    "You categorize a snippet of Azure policy text into EXACTLY ONE of the "
    "categories listed below. Output ONLY the category name verbatim — no prose, "
    "no quotes, no JSON. If the text is genuinely cross-cutting and does not "
    "fit any specific bucket, output \"general\". Always prefer a specific "
    "category over \"general\" when one applies.\n\n"
    "Categories:\n"
    f"{categories_for_prompt()}"
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
