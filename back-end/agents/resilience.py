"""Resilience primitives — circuit breaker + async retry with backoff."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from .errors import CircuitOpenError

logger = logging.getLogger(__name__)

TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,
)


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 3, recovery_seconds: float = 30.0):
        self._t = failure_threshold
        self._r = recovery_seconds
        self._f = 0
        self._state = self.CLOSED
        self._opened_at: float | None = None
        self._probe = False

    @property
    def state(self) -> str:
        if self._state == self.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self._r:
                return self.HALF_OPEN
        return self._state

    @property
    def recovery_remaining(self) -> float:
        if self._state != self.OPEN or self._opened_at is None:
            return 0.0
        return max(0.0, self._r - (time.monotonic() - self._opened_at))

    def check(self) -> None:
        s = self.state
        if s == self.OPEN:
            raise CircuitOpenError(self.recovery_remaining)
        if s == self.HALF_OPEN and self._probe:
            raise CircuitOpenError(0.0)
        if s == self.HALF_OPEN:
            self._probe = True

    def record_success(self) -> None:
        self._f = 0
        self._state = self.CLOSED
        self._opened_at = None
        self._probe = False

    def record_failure(self) -> None:
        self._f += 1
        self._probe = False
        if self._f >= self._t:
            self._state = self.OPEN
            self._opened_at = time.monotonic()


async def async_retry_with_backoff(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    deadline: float | None = None,
    circuit_breaker: CircuitBreaker | None = None,
    **kwargs: Any,
) -> Any:
    start = time.monotonic()
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        if deadline is not None and time.monotonic() - start >= deadline:
            raise last_exc or asyncio.TimeoutError("deadline exceeded")
        if circuit_breaker is not None:
            circuit_breaker.check()
        try:
            res = await fn(*args, **kwargs)
            if circuit_breaker is not None:
                circuit_breaker.record_success()
            return res
        except TRANSIENT_EXCEPTIONS as e:
            last_exc = e
            if circuit_breaker is not None:
                circuit_breaker.record_failure()
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            if deadline is not None:
                remaining = deadline - (time.monotonic() - start)
                if delay >= remaining:
                    raise
                delay = min(delay, remaining - 0.1)
            logger.warning("[RETRY] %d/%d failed: %s. sleeping %.1fs",
                           attempt + 1, max_retries, e, delay)
            await asyncio.sleep(delay)
    raise last_exc  # pragma: no cover
