"""Provision (or update) the ARB Bot Foundry v2 hosted prompt agents."""

from __future__ import annotations

import logging
import os
import sys

from azure.identity import DefaultAzureCredential

from agents.config import Config
from agents.iac_agent import SYSTEM_PROMPT as IAC_PROMPT
from agents.validate_agent import SYSTEM_PROMPT as VALIDATE_PROMPT

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("create_agents")


def main() -> int:
    cfg = Config()
    cfg.require_runtime()

    try:
        from azure.ai.projects import AIProjectClient
        from azure.ai.projects.models import PromptAgentDefinition
    except ImportError as e:
        log.error("azure-ai-projects v2 not installed: %s", e)
        return 2

    client = AIProjectClient(
        endpoint=cfg.foundry_project_endpoint,
        credential=DefaultAzureCredential(),
    )

    tools: list[dict] = []
    if cfg.foundry_search_connection_id:
        tools.append({
            "type": "azure_ai_search",
            "azure_ai_search": {
                "indexes": [{
                    "project_connection_id": cfg.foundry_search_connection_id,
                    "index_name": cfg.azure_search_index,
                    "query_type": "vector_semantic_hybrid",
                }],
            },
        })
    else:
        log.warning(
            "FOUNDRY_SEARCH_CONNECTION_ID not set — agents created without AI Search tool. "
            "Wire it up after SEARCH-REFACTOR ships."
        )

    vdef = PromptAgentDefinition(model=cfg.foundry_model_deployment,
                                 instructions=VALIDATE_PROMPT)
    if tools:
        vdef["tools"] = tools
    log.info("Creating/updating %s", cfg.validate_agent_name)
    v = client.agents.create_version(
        agent_name=cfg.validate_agent_name,
        definition=vdef,
        description="Validates ARB sections against the policy index.",
    )
    log.info("  %s version=%s", v.name, v.version)

    idef = PromptAgentDefinition(model=cfg.foundry_model_deployment,
                                 instructions=IAC_PROMPT)
    idef["tools"] = [{"type": "code_interpreter"}]
    log.info("Creating/updating %s", cfg.iac_agent_name)
    i = client.agents.create_version(
        agent_name=cfg.iac_agent_name,
        definition=idef,
        description="Generates starter Terraform AWS scripts from ASD content.",
    )
    log.info("  %s version=%s", i.name, i.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
