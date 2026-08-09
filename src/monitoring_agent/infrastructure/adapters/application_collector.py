from __future__ import annotations

from typing import Any, Callable, Iterable

from monitoring_agent.domain.entities import (
    CollectorResponse,
    CollectorStatus,
    Metric,
    Observation,
    Resource,
    ResourceType,
    Severity,
)
from monitoring_agent.infrastructure.adapters.base_collector import BaseCollector


ALL_METRICS = (
    "request_count",
    "error_rate",
    "latency_p95_ms",
    "latency_p50_ms",
    "throughput_rps",
    "success_ratio",
    "db_query_count",
    "db_slow_query_count",
)


class ApplicationCollector(BaseCollector):
    """Collect application/API metrics using mock data.

    - Async
    - Returns standardized `CollectorResponse`
    - Configurable which metrics to collect via `enabled_metrics`
    - Uses a mock provider by default; can be replaced with a real metrics backend
      (Prometheus, OpenTelemetry, etc.) later.
    """

    def __init__(
        self,
        *,
        service_name: str,
        enabled_metrics: Iterable[str] | None = None,
        mock_metrics_provider: Callable[[], dict] | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            name="application-collector",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

        if not service_name or not service_name.strip():
            raise ValueError("service_name must not be empty")

        self.service_name = service_name
        self.enabled_metrics = set(enabled_metrics or ALL_METRICS)
        self._mock_metrics_provider = mock_metrics_provider or self._default_mock_metrics

    async def collect(self) -> CollectorResponse:
        return await self._execute_with_retry(self._collect_once, operation_name="collect")

    async def validate(self) -> bool:
        return bool(self.service_name and self.service_name.strip())

    async def health_check(self) -> bool:
        return await self.validate()

    async def _collect_once(self) -> CollectorResponse:
        try:
            raw = self._mock_metrics_provider()
        except Exception as exc:  # pragma: no cover - defensive
            return CollectorResponse(
                collector_name=self.name,
                status=CollectorStatus.FAILED,
                error=f"failed to collect application metrics: {exc}",
            )

        # Build a service resource
        resource = Resource(
            resource_id=self.service_name,
            name=self.service_name,
            type=ResourceType.SERVICE,
            labels={"service": self.service_name},
            metadata={"collector": self.name},
        )

        observations: list[Observation] = []

        # Top-level metrics
        for metric_name in ALL_METRICS:
            if metric_name not in self.enabled_metrics:
                continue

            value = raw.get(metric_name)
            if value is None:
                continue

            try:
                v = float(value)
            except Exception:
                continue

            unit = "count" if metric_name.endswith("count") or metric_name == "request_count" else (
                "%" if metric_name in ("error_rate", "success_ratio") else ("ms" if "latency" in metric_name else ("rps" if metric_name == "throughput_rps" else "unknown"))
            )

            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name=metric_name,
                        value=v,
                        unit=unit,
                        description=f"Application metric {metric_name}",
                        labels={"service": self.service_name},
                    ),
                    severity=self._severity_for_metric(metric_name, v),
                )
            )

        # Per-endpoint (API) metrics
        if "endpoints" in raw and "request_count" in self.enabled_metrics:
            endpoints = raw.get("endpoints", [])
            for ep in endpoints:
                name = ep.get("name") or ep.get("path") or "unknown"
                ep_resource = Resource(
                    resource_id=f"{self.service_name}:endpoint:{name}",
                    name=name,
                    type=ResourceType.API,
                    labels={"service": self.service_name, "endpoint": name},
                    metadata={"collector": self.name},
                )

                observations.append(
                    Observation(
                        resource=ep_resource,
                        metric=Metric(
                            name="api_request_count",
                            value=float(ep.get("request_count", 0)),
                            unit="count",
                            description="Per-endpoint request count",
                            labels={"endpoint": name},
                        ),
                        severity=Severity.INFO,
                    )
                )

                if "error_rate" in ep and "error_rate" in self.enabled_metrics:
                    observations.append(
                        Observation(
                            resource=ep_resource,
                            metric=Metric(
                                name="api_error_rate",
                                value=float(ep.get("error_rate", 0.0)),
                                unit="%",
                                description="Per-endpoint error rate",
                                labels={"endpoint": name},
                            ),
                            severity=Severity.WARNING if float(ep.get("error_rate", 0.0)) > 5.0 else Severity.INFO,
                        )
                    )

        return CollectorResponse(
            collector_name=self.name,
            status=CollectorStatus.SUCCESS,
            resources=[resource],
            observations=observations,
            health_scores=[],
        )

    def _severity_for_metric(self, metric_name: str, value: float) -> Severity:
        if metric_name == "error_rate":
            return Severity.CRITICAL if value >= 50.0 else (Severity.ERROR if value >= 20.0 else (Severity.WARNING if value >= 5.0 else Severity.INFO))
        if metric_name in ("latency_p95_ms", "latency_p50_ms"):
            return Severity.WARNING if value >= 1000.0 else Severity.INFO
        if metric_name == "throughput_rps":
            return Severity.INFO
        if metric_name == "success_ratio":
            return Severity.WARNING if value < 90.0 else Severity.INFO
        return Severity.INFO

    def _default_mock_metrics(self) -> dict:
        return {
            "request_count": 12456,
            "error_rate": 2.3,
            "latency_p95_ms": 320.5,
            "latency_p50_ms": 45.2,
            "throughput_rps": 52.4,
            "success_ratio": 97.7,
            "db_query_count": 34567,
            "db_slow_query_count": 12,
            "endpoints": [
                {"name": "GET /v1/items", "request_count": 8000, "error_rate": 1.2},
                {"name": "POST /v1/items", "request_count": 4000, "error_rate": 3.8},
            ],
        }


__all__ = ["ApplicationCollector"]
