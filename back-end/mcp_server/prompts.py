"""MCP prompts for the ARB Bot — reusable templates Copilot Studio can surface.

Prompts are static templates: they describe the workflow but do NOT execute
tools themselves. Copilot Studio uses them as conversation entry points.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Register the ARB Bot prompt templates on ``mcp``."""

    @mcp.prompt(
        name="review_architecture",
        description=(
            "Guided ARB review of an Architecture Design Document. The "
            "assistant should call validate_arb on the attached/mentioned "
            "file and summarize the findings by Type."
        ),
    )
    def review_architecture(filename: str = "the attached document") -> str:
        return (
            f"Please review {filename} as an Azure Architecture Review Board "
            "reviewer.\n\n"
            "Steps:\n"
            "1. Call the validate_arb tool with the file (use file_reference "
            "   if it came from SharePoint, otherwise inline bytes).\n"
            "2. Group the findings by Type: Violation, Deviation, Suggestion, "
            "   Missing.\n"
            "3. For each Violation and Mandatory Missing, briefly explain the "
            "   policy gap and quote the principle name.\n"
            "4. Close with a one-line verdict (ready / needs revision)."
        )

    @mcp.prompt(
        name="explain_finding",
        description=(
            "Explain a single validation finding with its policy citation. "
            "Useful for drilling into one row from a validate_arb result."
        ),
    )
    def explain_finding(
        finding_type: str,
        issue: str,
        principle: str,
    ) -> str:
        return (
            f"Explain the following ARB finding to an Azure architect:\n\n"
            f"- Type: {finding_type}\n"
            f"- Issue: {issue}\n"
            f"- Principle: {principle}\n\n"
            "Steps:\n"
            "1. Call search_policies with `principle` as the query (and the "
            "   finding's Category as the filter when known).\n"
            "2. Quote the most relevant policy snippet and cite its header.\n"
            "3. Recommend a concrete remediation aligned with the policy."
        )

    @mcp.prompt(
        name="draft_iac",
        description=(
            "Produce Infrastructure-as-Code (Terraform) for an approved "
            "Architecture Design Document by calling the generate_iac tool."
        ),
    )
    def draft_iac(filename: str = "the attached document") -> str:
        return (
            f"Generate Infrastructure-as-Code for {filename}.\n\n"
            "Steps:\n"
            "1. Call the generate_iac tool with the file.\n"
            "2. Present each returned script in a fenced code block "
            "   labelled with its target resource.\n"
            "3. Add a short 'Apply with' note describing the terraform "
            "   commands the architect should run."
        )
