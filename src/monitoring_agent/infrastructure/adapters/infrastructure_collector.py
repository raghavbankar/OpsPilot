from __future__ import annotations

from typing import Any, Callable, Mapping

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


class InfrastructureCollector(BaseCollector):
    """Collect infrastructure telemetry using mock data and emit standardized observations."""

    def __init__(
        self,
        *,
        host_id: str,
        thresholds: Mapping[str, float] | None = None,
        mock_data_provider: Callable[[], dict[str, float]] | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            name="infrastructure-collector",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        if not host_id.strip():
            raise ValueError("host_id must not be empty")

        self.host_id = host_id
        self.thresholds = dict(thresholds or {})
        self._mock_data_provider = mock_data_provider or self._default_mock_data

    async def collect(self) -> CollectorResponse:
        """Collect infrastructure telemetry and return a standardized response."""
        return await self._execute_with_retry(self._collect_once, operation_name="collect")

    async def validate(self) -> bool:
        """Validate collector configuration and threshold values."""
        if not self.host_id.strip():
            return False
        return all(isinstance(value, (int, float)) for value in self.thresholds.values())

    async def health_check(self) -> bool:
        """Report whether the collector is ready for use."""
        return await self.validate()

    async def _collect_once(self) -> CollectorResponse:
        try:
            raw_metrics = self._mock_data_provider()
        except Exception as exc:  # pragma: no cover - defensive fallback
            return CollectorResponse(
                collector_name=self.name,
                status=CollectorStatus.FAILED,
                error=f"failed to collect infrastructure telemetry: {exc}",
            )

        normalized_metrics = self._normalize_metrics(raw_metrics)
        resource = Resource(
            resource_id=self.host_id,
            name=f"host:{self.host_id}",
            type=ResourceType.HOST,
            labels={"host_id": self.host_id},
            metadata={"collector": self.name},
        )

        observations: list[Observation] = []
        for metric_name, value in normalized_metrics.items():
            severity = self._severity_for(metric_name, value)
            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name=metric_name,
                        value=float(value),
                        unit=self._unit_for(metric_name),
                        description=self._description_for(metric_name),
                        labels={"host_id": self.host_id},
                    ),
                    severity=severity,
                    message=self._message_for(metric_name, value, severity),
                )
            )

        return CollectorResponse(
            collector_name=self.name,
            status=CollectorStatus.SUCCESS,
            resources=[resource],
            observations=observations,
            health_scores=[],
        )

    def _default_mock_data(self) -> dict[str, float]:
        return {
            "cpu_percent": 72.3,
            "memory_percent": 68.4,
            "disk_percent": 71.8,
            "network_rx_mbps": 55.2,
            "process_count": 164.0,
        }

    def _normalize_metrics(self, raw_metrics: Mapping[str, Any]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for metric_name in (
            "cpu_percent",
            "memory_percent",
            "disk_percent",
            "network_rx_mbps",
            "process_count",
        ):
            if metric_name in raw_metrics:
                value = raw_metrics[metric_name]
                if isinstance(value, bool):
                    raise ValueError(f"metric {metric_name} cannot be boolean")
                normalized[metric_name] = float(value)
            else:
                normalized[metric_name] = self._default_mock_data()[metric_name]
        return normalized

    def _severity_for(self, metric_name: str, value: float) -> Severity:
        threshold = self.thresholds.get(metric_name)
        if threshold is None:
            return Severity.INFO
        return Severity.WARNING if value >= threshold else Severity.INFO

    def _unit_for(self, metric_name: str) -> str:
        units = {
            "cpu_percent": "%",
            "memory_percent": "%",
            "disk_percent": "%",
            "network_rx_mbps": "Mbps",
            "process_count": "count",
        }
        return units.get(metric_name, "unknown")

    def _description_for(self, metric_name: str) -> str:
        descriptions = {
            "cpu_percent": "CPU utilization",
            "memory_percent": "Memory utilization",
            "disk_percent": "Disk utilization",
            "network_rx_mbps": "Network receive throughput",
            "process_count": "Number of running processes",
        }
        return descriptions.get(metric_name, "Infrastructure metric")

    def _message_for(self, metric_name: str, value: float, severity: Severity) -> str:
        if severity is Severity.WARNING:
            return f"{metric_name} exceeded the configured threshold"
        return f"{metric_name} collected successfully"


__all__ = ["InfrastructureCollector"]
