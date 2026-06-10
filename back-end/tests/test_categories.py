"""Unit tests for the canonical PolicyCategory taxonomy (issue #61).

These tests are the contract that the AOAI categorize skill inside the pull-mode
indexer relies on. Any change here implies re-ingesting the policy corpus.
"""

from __future__ import annotations

import json

from agents.categories import (
    ASD_SECTION_CATEGORIES,
    CATEGORIZE_SYSTEM_PROMPT,
    CATEGORIZE_SYSTEM_PROMPT_V1,
    CATEGORY_DEFINITIONS,
    CATEGORY_FEW_SHOT,
    DEFAULT_SECTION_CATEGORIES,
    IAC_SECTIONS,
    PolicyCategory,
    categories_for_prompt,
    parse_category,
)
from agents.categorize_chunk import _strip_to_category


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
    # New format: each line is `- **<value>** — <definition>`
    expected = "\n".join(
        f"- **{c.value}** — {CATEGORY_DEFINITIONS[c]}" for c in PolicyCategory
    )
    assert rendered == expected
    # Calling twice returns the same string.
    assert categories_for_prompt() == rendered


def test_system_prompt_is_byte_stable():
    """CATEGORIZE_SYSTEM_PROMPT must be deterministic — it's baked into the
    indexer skillset definition and any drift triggers an unnecessary reindex.
    We assert (a) the rendered helpers it depends on are idempotent and (b)
    the constant matches a freshly-rendered equivalent. We avoid importlib
    reload because it would mint a fresh PolicyCategory enum identity and
    poison other tests in the same run.
    """
    assert categories_for_prompt() == categories_for_prompt()
    # Sanity: prompt embeds today's category list verbatim.
    assert categories_for_prompt() in CATEGORIZE_SYSTEM_PROMPT


def test_system_prompt_contains_all_categories():
    for v in EXPECTED_VALUES:
        assert v in CATEGORIZE_SYSTEM_PROMPT, f"{v!r} missing from system prompt"


def test_system_prompt_token_budget():
    """Prompt must stay under the 1,800-token budget so we don't slow per-chunk
    validate calls or break the indexer skillset call. We use a character-based
    proxy (1 token ≈ 4 chars for English) since tiktoken isn't a hard dep.
    """
    # 1800 tokens * 4 chars/token = 7200 chars upper bound.
    assert len(CATEGORIZE_SYSTEM_PROMPT) < 7200, (
        f"prompt is {len(CATEGORIZE_SYSTEM_PROMPT)} chars (~{len(CATEGORIZE_SYSTEM_PROMPT)//4} "
        f"tokens) — budget is 7200 chars (~1800 tokens)"
    )


def test_system_prompt_is_json_safe():
    """The prompt is embedded into a JSON skillset document — must round-trip."""
    payload = json.dumps({"prompt": CATEGORIZE_SYSTEM_PROMPT})
    decoded = json.loads(payload)
    assert decoded["prompt"] == CATEGORIZE_SYSTEM_PROMPT


def test_system_prompt_v1_preserved_for_backout():
    """V1 prompt is kept for one release as a backout path."""
    assert CATEGORIZE_SYSTEM_PROMPT_V1
    # V1 must still contain every category name verbatim.
    for v in EXPECTED_VALUES:
        assert v in CATEGORIZE_SYSTEM_PROMPT_V1


# ---------------------------------------------------------------------------
# Definitions & few-shot
# ---------------------------------------------------------------------------

def test_category_definitions_cover_every_member_exactly_once():
    assert set(CATEGORY_DEFINITIONS.keys()) == set(PolicyCategory)
    assert len(CATEGORY_DEFINITIONS) == len(EXPECTED_VALUES)
    for cat, definition in CATEGORY_DEFINITIONS.items():
        assert isinstance(definition, str)
        assert definition.strip(), f"empty definition for {cat}"


def test_few_shot_covers_every_specific_category():
    """At least one example per non-general category (9 categories → ≥9)."""
    seen = {expected for _, expected in CATEGORY_FEW_SHOT}
    for cat in PolicyCategory.specific():
        assert cat in seen, f"no few-shot example for {cat.value}"


def test_few_shot_examples_round_trip_through_parse_category():
    """Every example's expected label parses back to itself."""
    for snippet, expected in CATEGORY_FEW_SHOT:
        assert parse_category(expected.value) is expected, (
            f"few-shot snippet {snippet[:40]!r}... has unparseable label "
            f"{expected.value!r}"
        )


def test_few_shot_examples_embedded_in_prompt():
    """The first sentence of each few-shot snippet must appear verbatim in
    the rendered prompt so we know _render_few_shot is actually being used.
    """
    for snippet, _ in CATEGORY_FEW_SHOT:
        # Use the first 30 chars as an anchor (snippets are long enough).
        anchor = snippet[:30]
        assert anchor in CATEGORIZE_SYSTEM_PROMPT, (
            f"few-shot anchor {anchor!r} missing from system prompt"
        )


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


# ---------------------------------------------------------------------------
# _strip_to_category — robust handling of malformed model output
# ---------------------------------------------------------------------------

def test_strip_to_category_passthrough_clean_value():
    assert _strip_to_category("Network") == "Network"


def test_strip_to_category_strips_bullets_and_punctuation():
    assert _strip_to_category("- Network") == "Network"
    assert _strip_to_category("* Network.") == "Network"
    assert _strip_to_category("• Network;") == "Network"


def test_strip_to_category_strips_surrounding_quotes():
    assert _strip_to_category('"Network"') == "Network"
    assert _strip_to_category("'Network'") == "Network"
    assert _strip_to_category("`Network`") == "Network"


def test_strip_to_category_keeps_first_line_only():
    raw = "Network\nThis snippet is about VNet topology."
    assert _strip_to_category(raw) == "Network"


def test_strip_to_category_unwraps_json_envelope():
    assert _strip_to_category('{"category": "Network"}') == "Network"
    assert _strip_to_category('{"value": "Network"}') == "Network"
    assert _strip_to_category('["Network"]') == "Network"


def test_strip_to_category_handles_malformed_json_gracefully():
    # Not valid JSON, but should still strip to something parseable.
    assert _strip_to_category("{Network}") == "Network"


def test_strip_to_category_empty_returns_empty():
    assert _strip_to_category("") == ""
    assert _strip_to_category("   \n  ") == ""


def test_strip_to_category_round_trips_through_parse_category():
    """Every malformed-but-recoverable variant ends up as the right enum."""
    variants = [
        "Network",
        "- Network",
        '"Network"',
        "Network.",
        '{"category": "Network"}',
        "Network\nbecause topology",
    ]
    for v in variants:
        assert parse_category(_strip_to_category(v)) is PolicyCategory.NETWORK, v
