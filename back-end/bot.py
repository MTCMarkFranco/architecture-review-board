"""Bot Framework Skill handler for Copilot Studio integration.

Handles incoming Bot Framework Activities (event/message) and routes
them to the ARB validation and IaC generation workflows.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any

from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import Activity, ActivityTypes, EndOfConversationCodes

from agents.config import Config
from agents.orchestrator import ArbWorkflow

logger = logging.getLogger(__name__)

# Lazy-init workflow (same pattern as app.py)
_workflow: ArbWorkflow | None = None


def _get_workflow() -> ArbWorkflow:
    global _workflow
    if _workflow is None:
        _workflow = ArbWorkflow(config=Config())
    return _workflow


class ARBSkillBot(ActivityHandler):
    """Bot Framework skill that validates architecture documents and generates IaC."""

    async def on_event_activity(self, turn_context: TurnContext):
        """Route event activities by name to the appropriate handler."""
        activity = turn_context.activity
        event_name = activity.name or ""
        value = activity.value or {}

        logger.info("Skill event received: name=%s", event_name)

        if event_name == "validateArchitectureDocument":
            result = await self._handle_validate(value)
        elif event_name == "generateInfrastructureAsCode":
            result = await self._handle_geniac(value)
        else:
            result = {"error": f"Unknown event: {event_name}"}

        # Send EndOfConversation with result value
        end_activity = Activity(
            type=ActivityTypes.end_of_conversation,
            code=EndOfConversationCodes.completed_successfully,
            value=result,
        )
        await turn_context.send_activity(end_activity)

    async def on_message_activity(self, turn_context: TurnContext):
        """Handle message activities (text-based invocation)."""
        text = (turn_context.activity.text or "").strip().lower()
        value = turn_context.activity.value or {}

        # If value contains file data, try to determine intent from text
        if "validate" in text:
            result = await self._handle_validate(value)
        elif "iac" in text or "terraform" in text or "generate" in text:
            result = await self._handle_geniac(value)
        else:
            result = {
                "message": "Send an event with name 'validateArchitectureDocument' "
                "or 'generateInfrastructureAsCode' with file_base64 and filename in the value."
            }

        end_activity = Activity(
            type=ActivityTypes.end_of_conversation,
            code=EndOfConversationCodes.completed_successfully,
            value=result,
        )
        await turn_context.send_activity(end_activity)

    async def on_end_of_conversation_activity(self, turn_context: TurnContext):
        """Respond to EndOfConversation — used by Copilot Studio health check."""
        logger.info("Skill health check (EndOfConversation) received")
        end_activity = Activity(
            type=ActivityTypes.end_of_conversation,
            code=EndOfConversationCodes.completed_successfully,
        )
        await turn_context.send_activity(end_activity)

    # ------------------------------------------------------------------
    # Business logic handlers
    # ------------------------------------------------------------------

    async def _handle_validate(self, value: dict) -> dict[str, Any]:
        file_base64 = value.get("file_base64", "")
        filename = value.get("filename", "")

        if not file_base64 or not filename:
            return {"error": "Missing required fields: file_base64 and filename"}

        try:
            file_bytes = base64.b64decode(file_base64)
        except Exception as e:
            return {"error": f"Invalid base64: {e}"}

        try:
            findings = await _get_workflow().validate_bytes(file_bytes, filename)
            return {"findings": findings}
        except Exception as e:
            logger.exception("Validation failed")
            return {"error": str(e)}

    async def _handle_geniac(self, value: dict) -> dict[str, Any]:
        file_base64 = value.get("file_base64", "")
        filename = value.get("filename", "")

        if not file_base64 or not filename:
            return {"error": "Missing required fields: file_base64 and filename"}

        try:
            file_bytes = base64.b64decode(file_base64)
        except Exception as e:
            return {"error": f"Invalid base64: {e}"}

        try:
            scripts = await _get_workflow().iac_bytes(file_bytes, filename)
            return {"scripts": scripts}
        except Exception as e:
            logger.exception("IaC generation failed")
            return {"error": str(e)}
