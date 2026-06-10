"""MAF SequentialBuilder orchestrator for ARB Bot.

For the current ARB flow we have two independent endpoints:
  - /validatearb → validate_arb_sections
  - /geniac      → generate_iac

The validate stage is itself fan-out/fan-in across sections; the geniac stage
is a single agent call. A SequentialBuilder pipeline is offered for callers
that want to chain validate → iac, which is occasionally useful for
end-to-end CI smoke tests.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from .config import Config
from .errors import WorkflowError, WorkflowTimeoutError
from .iac_agent import generate_iac
from .resilience import CircuitBreaker, async_retry_with_backoff
from .validate_agent import (
    build_project_client,
    validate_arb_chunks,
    validate_arb_sections,
)

logger = logging.getLogger(__name__)


class ArbWorkflow:
    """Resilient wrapper around the validate + iac agents."""

    def __init__(self, config: Config | None = None,
                 client: Any | None = None,
                 breaker: CircuitBreaker | None = None):
        self.config = config or Config()
        self._client = client
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=self.config.circuit_breaker_threshold,
            recovery_seconds=self.config.circuit_breaker_recovery_seconds,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = build_project_client(self.config)
        return self._client

    async def validate(self, arb: dict[str, Any]) -> list[dict]:
        """Section-based validate. Retained for callers that already parse the
        ASD into a dict (legacy tests, programmatic users). New callers should
        prefer :meth:`validate_bytes` so the doc is semantically chunked instead
        of split by hard-coded section names.
        """
        cid = uuid.uuid4().hex[:8]
        start = time.monotonic()
        logger.info("[ARB:%s] validate(sections) start", cid)
        try:
            result = await async_retry_with_backoff(
                lambda: asyncio.wait_for(
                    validate_arb_sections(arb, self.config, self._get_client()),
                    timeout=self.config.timeout_seconds,
                ),
                max_retries=self.config.retry_count,
                base_delay=self.config.retry_base_delay,
                deadline=self.config.timeout_seconds,
                circuit_breaker=self._breaker,
            )
        except asyncio.TimeoutError as e:
            raise WorkflowTimeoutError(self.config.timeout_seconds) from e
        except WorkflowError:
            raise
        except Exception as e:  # noqa: BLE001
            raise WorkflowError(f"validate failed: {e}") from e
        logger.info("[ARB:%s] validate(sections) ok in %.2fs (%d findings)",
                    cid, time.monotonic() - start, len(result))
        return result

    async def validate_bytes(self, file_bytes: bytes,
                             filename: str | None = None) -> list[dict]:
        """Chunk-based validate. Sole entry point used by the /validatearb API.

        Semantically chunks the uploaded PDF/DOCX via Document Intelligence,
        AOAI-categorizes each chunk, runs a filtered hybrid + semantic search,
        and fans out to ValidateArbAgent per chunk. See
        :func:`validate_agent.validate_arb_chunks` for the design.
        """
        cid = uuid.uuid4().hex[:8]
        start = time.monotonic()
        logger.info("[ARB:%s] validate(chunks) start filename=%s bytes=%d",
                    cid, filename, len(file_bytes))
        try:
            result = await async_retry_with_backoff(
                lambda: asyncio.wait_for(
                    validate_arb_chunks(file_bytes, filename,
                                        self.config, self._get_client()),
                    timeout=self.config.timeout_seconds,
                ),
                max_retries=self.config.retry_count,
                base_delay=self.config.retry_base_delay,
                deadline=self.config.timeout_seconds,
                circuit_breaker=self._breaker,
            )
        except asyncio.TimeoutError as e:
            raise WorkflowTimeoutError(self.config.timeout_seconds) from e
        except WorkflowError:
            raise
        except Exception as e:  # noqa: BLE001
            raise WorkflowError(f"validate failed: {e}") from e
        logger.info("[ARB:%s] validate(chunks) ok in %.2fs (%d findings)",
                    cid, time.monotonic() - start, len(result))
        return result

    async def iac(self, arb: dict[str, Any]) -> list[str]:
        cid = uuid.uuid4().hex[:8]
        start = time.monotonic()
        logger.info("[ARB:%s] iac start", cid)
        try:
            result = await async_retry_with_backoff(
                lambda: asyncio.wait_for(
                    generate_iac(arb, self.config, self._get_client()),
                    timeout=self.config.timeout_seconds,
                ),
                max_retries=self.config.retry_count,
                base_delay=self.config.retry_base_delay,
                deadline=self.config.timeout_seconds,
                circuit_breaker=self._breaker,
            )
        except asyncio.TimeoutError as e:
            raise WorkflowTimeoutError(self.config.timeout_seconds) from e
        except WorkflowError:
            raise
        except Exception as e:  # noqa: BLE001
            raise WorkflowError(f"iac failed: {e}") from e
        logger.info("[ARB:%s] iac ok in %.2fs (%d scripts)",
                    cid, time.monotonic() - start, len(result))
        return result

    async def sequential(self, arb: dict[str, Any]) -> dict[str, Any]:
        """Run validate then iac using MAF SequentialBuilder.

        Falls back to direct calls if ``agent_framework`` is not installed
        (e.g. unit-test environments without the dependency).
        """
        try:
            from agent_framework.orchestrations import SequentialBuilder  # noqa: F401
        except ImportError:
            findings = await self.validate(arb)
            scripts = await self.iac(arb)
            return {"findings": findings, "iac": scripts}

        # When MAF is available, use the same direct calls; SequentialBuilder
        # is reserved for true hosted-agent-to-hosted-agent chaining which our
        # current contract does not require (the two stages consume different
        # inputs).
        findings = await self.validate(arb)
        scripts = await self.iac(arb)
        return {"findings": findings, "iac": scripts}
