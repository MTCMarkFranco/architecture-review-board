"""Legacy keyword-rule policy categoriser.

.. deprecated::
   Per #61 the pull-mode indexer categorises each chunk via the AOAI categorize
   skill inside the search skillset (see :mod:`agents.categories`). This module
   is kept for two narrow uses:

   1. Offline migration / smoke scripts that synthesise a category from a
      policy header without round-tripping to AOAI.
   2. A last-resort fallback if a search hit's ``category`` field is ever empty
      (it should not be, post-pull-indexer).

   New code should import :data:`agents.categories.PolicyCategory` directly.
"""

from __future__ import annotations

from agents.categories import PolicyCategory

_RULES: list[tuple[tuple[str, ...], str]] = [
    (("identity", "access ", "iam", "rbac", "authentic"), PolicyCategory.IDENTITY_AND_ACCESS.value),
    (("network", "firewall", "segment", "subnet", "private endpoint"), PolicyCategory.NETWORK.value),
    (("storage", "backup", "blob", "disk"), PolicyCategory.STORAGE_AND_DATA.value),
    (("database", "data platform", "sql", "cosmos"), PolicyCategory.STORAGE_AND_DATA.value),
    (("encrypt", "data protection"), PolicyCategory.STORAGE_AND_DATA.value),
    (("cost", "tagging", "billing", "naming"), PolicyCategory.COST_OPTIMIZATION.value),
    (("observ", "monitor", "logging", "telemetry"), PolicyCategory.OPERATIONAL_EXCELLENCE.value),
    (("devops", "ci", "cd", "infrastructure as code", "iac"), PolicyCategory.OPERATIONAL_EXCELLENCE.value),
    (("performance", "efficien", "scal"), PolicyCategory.PERFORMANCE_AND_EFFICIENCY.value),
    (("reliab", "disaster", "continuity", "availab"), PolicyCategory.RELIABILITY.value),
    (("security", "governance", "compliance", "policy"), PolicyCategory.SECURITY_AND_GOVERNANCE.value),
    (("ai ", "agent", "model", "ml"), PolicyCategory.AI_WORKLOADS.value),
    (("compute", "vm ", "hardening", "container", "kubernetes", "serverless", "app service"), PolicyCategory.OPERATIONAL_EXCELLENCE.value),
]


def derive_category(header: str) -> str:
    """Return a deterministic category from a policy header.

    Falls back to :attr:`PolicyCategory.GENERAL` when nothing matches.
    Legacy — see module docstring.
    """
    h = (header or "").lower()
    for keywords, category in _RULES:
        if any(k in h for k in keywords):
            return category
    return PolicyCategory.GENERAL.value

