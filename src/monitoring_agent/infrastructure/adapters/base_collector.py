from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from monitoring_agent.core.logging import logger as app_logger
from monitoring_agent.domain.entities import CollectorResponse, CollectorStatus


class BaseCollector(ABC):
    """Abstract base class for all monitoring collectors.

    Concrete collectors inherit from this class and implement the specific
    collection, validation, and health-check behavior for a backend or system.
    """

    def __init__(
        self,
        *,
        name: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
        logger: logging.Logger | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")

        self.name = name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.logger = logger or app_logger.bind(component="collector", collector=name)

    @abstractmethod
    async def collect(self) -> CollectorResponse:
        """Collect observations and return a collector response."""

    @abstractmethod
    async def validate(self) -> bool:
        """Validate that the collector is configured and usable."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Report whether the collector is healthy."""

    async def _execute_with_retry(
        self,
        operation: Callable[[], Awaitable[CollectorResponse]],
        *,
        operation_name: str,
    ) -> CollectorResponse:
        """Execute an operation with retry, timeout, and logging safeguards."""

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                self.logger.debug(
                    "collector_operation_started",
                    collector=self.name,
                    operation=operation_name,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                )
                return await asyncio.wait_for(operation(), timeout=self.timeout_seconds)
            except asyncio.TimeoutError as exc:
                last_error = exc
                self.logger.warning(
                    "collector_operation_timed_out",
                    collector=self.name,
                    operation=operation_name,
                    attempt=attempt + 1,
                    timeout_seconds=self.timeout_seconds,
                )
            except Exception as exc:  # pragma: no cover - defensive fallback
                last_error = exc
                self.logger.exception(
                    "collector_operation_failed",
                    collector=self.name,
                    operation=operation_name,
                    attempt=attempt + 1,
                    error=str(exc),
                )

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1))

        if last_error is None:
            raise RuntimeError(f"Collector {self.name} failed without a captured error")

        return CollectorResponse(
            collector_name=self.name,
            status=CollectorStatus.FAILED,
            error=str(last_error),
        )

    async def _run_validation(self) -> bool:
        """Convenience wrapper for validating collector readiness."""
        try:
            return await self.validate()
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.exception(
                "collector_validation_failed",
                collector=self.name,
                error=str(exc),
            )
            return False

    async def _run_health_check(self) -> bool:
        """Convenience wrapper for health checks with logging."""
        try:
            return await self.health_check()
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.exception(
                "collector_health_check_failed",
                collector=self.name,
                error=str(exc),
            )
            return False


__all__ = ["BaseCollector"]
