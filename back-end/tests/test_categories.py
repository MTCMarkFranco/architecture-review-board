"""Unit tests for the canonical PolicyCategory taxonomy (issue #61).

These tests are the contract that the AOAI categorize skill inside the pull-mode
indexer relies on. Any change here implies re-ingesting the policy corpus.
"""

from __future__ import annotations

import json

from agents.categories import (
    ASD_SECTION_CATEGORIES,
    CATEGORIZE_SYSTEM_PROMPT,
    DEFAULT_SECTION_CATEGORIES,
    IAC_SECTIONS,
    PolicyCategory,
    categories_for_prompt,
    parse_category,
)


# ---------------------------------------------------------------------------
# Enum stability
# ---------------------------------------------------------------------------

EXPECTED_VALUES = [
    "Identity and Access",
    "Network",
    "Storage and Data",
    "Cost Optimization",
    "Operational Excellence",
    "Performance and Efficiency",
    "Reliability",
    "Security and Governance",
    "AI Workloads",
    "general",
]


def test_policy_category_values_are_stable():
    """If this fails, you renamed/added a category — re-ingest the corpus."""
    assert PolicyCategory.values() == EXPECTED_VALUES


def test_policy_category_specific_excludes_general():
    specific = PolicyCategory.specific()
    assert PolicyCategory.GENERAL not in specific
    assert len(specific) == len(EXPECTED_VALUES) - 1


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_categories_for_prompt_is_byte_stable():
    """Skillset hash invalidation depends on this string being deterministic."""
    rendered = categories_for_prompt()
    assert rendered == "\n".join(f"- {v}" for v in EXPECTED_VALUES)
    # Calling twice returns the same string.
    assert categories_for_prompt() == rendered


def test_system_prompt_contains_all_categories():
    for v in EXPECTED_VALUES:
        assert v in CATEGORIZE_SYSTEM_PROMPT, f"{v!r} missing from system prompt"


def test_system_prompt_is_json_safe():
    """The prompt is embedded into a JSON skillset document — must round-trip."""
    payload = json.dumps({"prompt": CATEGORIZE_SYSTEM_PROMPT})
    decoded = json.loads(payload)
    assert decoded["prompt"] == CATEGORIZE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Section mappings
# ---------------------------------------------------------------------------

def test_every_section_maps_to_at_least_one_category():
    for section, cats in ASD_SECTION_CATEGORIES.items():
        assert cats, f"Section {section!r} maps to no categories"
        for c in cats:
            assert isinstance(c, PolicyCategory)


def test_default_section_categories_is_non_empty():
    assert DEFAULT_SECTION_CATEGORIES
    for c in DEFAULT_SECTION_CATEGORIES:
        assert isinstance(c, PolicyCategory)


def test_iac_sections_are_subset_of_asd_sections():
    """Every IaC section must also be a known ASD section so retrieval can route."""
    asd = set(ASD_SECTION_CATEGORIES.keys())
    for section in IAC_SECTIONS:
        assert section in asd, f"IaC section {section!r} missing from ASD_SECTION_CATEGORIES"


# ---------------------------------------------------------------------------
# parse_category
# ---------------------------------------------------------------------------

def test_parse_category_known_value():
    assert parse_category("Network") is PolicyCategory.NETWORK
    assert parse_category("  Network  ") is PolicyCategory.NETWORK


def test_parse_category_unknown_falls_back_to_general():
    assert parse_category("Not A Category") is PolicyCategory.GENERAL
    assert parse_category(None) is PolicyCategory.GENERAL
    assert parse_category("") is PolicyCategory.GENERAL
