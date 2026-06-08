"""Derive a stable policy category from a policy header string."""

from __future__ import annotations

_RULES: list[tuple[tuple[str, ...], str]] = [
    (("identity", "access ", "iam", "rbac", "authentic"), "Identity and Access"),
    (("network", "firewall", "segment", "subnet", "private endpoint"), "Network"),
    (("storage", "backup", "blob", "disk"), "Storage and Data"),
    (("database", "data platform", "sql", "cosmos"), "Storage and Data"),
    (("encrypt", "data protection"), "Storage and Data"),
    (("cost", "tagging", "billing", "naming"), "Cost Optimization"),
    (("observ", "monitor", "logging", "telemetry"), "Operational Excellence"),
    (("devops", "ci", "cd", "infrastructure as code", "iac"), "Operational Excellence"),
    (("performance", "efficien", "scal"), "Performance and Efficiency"),
    (("reliab", "disaster", "continuity", "availab"), "Reliability"),
    (("security", "governance", "compliance", "policy"), "Security and Governance"),
    (("ai ", "agent", "model", "ml"), "AI Workloads"),
    (("compute", "vm ", "hardening", "container", "kubernetes", "serverless", "app service"), "Operational Excellence"),
]


def derive_category(header: str) -> str:
    """Return a deterministic category from a policy header.

    Falls back to ``general`` when nothing matches.
    """
    h = (header or "").lower()
    for keywords, category in _RULES:
        if any(k in h for k in keywords):
            return category
    return "general"
