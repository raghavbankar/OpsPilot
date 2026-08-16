from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from monitoring_agent.domain.entities import CollectorResponse, Resource


class DataNormalizer:
    """Merge collector output into a unified monitoring snapshot.

    This normalizer intentionally does not calculate health, detect anomalies,
    or correlate data. It focuses on collecting a consistent, deduplicated view of
    observations from multiple collector responses.
    """

    _UNIT_ALIASES = {
        "percent": "%",
        "percentage": "%",
        "pct": "%",
        "ms": "ms",
        "millisecond": "ms",
        "milliseconds": "ms",
        "s": "s",
        "second": "s",
        "seconds": "s",
        "count": "count",
        "num": "count",
        "total": "count",
        "bytes": "bytes",
        "byte": "bytes",
        "kb": "KB",
        "mb": "MB",
        "gb": "GB",
        "tokens": "tokens",
        "token": "tokens",
        "usd": "USD",
        "dollar": "USD",
        "dollars": "USD",
    }

    _METRIC_ALIASES = {
        "latency p95": "latency_p95",
        "latency_p95": "latency_p95",
        "latency p99": "latency_p99",
        "latency_p99": "latency_p99",
        "error rate": "error_rate",
        "error_rate": "error_rate",
        "errorratio": "error_rate",
        "requests total": "requests_total",
        "requests_total": "requests_total",
        "request count": "request_count",
        "request_count": "request_count",
        "throughput rps": "throughput_rps",
        "throughput_rps": "throughput_rps",
        "cpu percent": "cpu_percent",
        "cpu_percent": "cpu_percent",
        "memory percent": "memory_percent",
        "memory_percent": "memory_percent",
    }

    @classmethod
    def normalize(cls, responses: Iterable[CollectorResponse | Mapping[str, Any]]) -> dict[str, Any]:
        resources: dict[str, Resource] = OrderedDict()
        metrics: list[dict[str, Any]] = []
        seen_signatures: set[str] = set()
        missing_metric_values = 0

        for response in responses:
            collector_name = "unknown"
            response_resources: list[Resource] = []
            response_observations: list[Any] = []

            if isinstance(response, CollectorResponse):
                collector_name = response.collector_name
                response_resources = list(response.resources)
                response_observations = list(response.observations)
            elif isinstance(response, Mapping):
                collector_name = str(response.get("collector_name") or response.get("collector") or "unknown")
                for resource in response.get("resources", []) or []:
                    response_resources.append(cls._coerce_resource(resource))
                for observation in response.get("observations", []) or []:
                    response_observations.append(observation)
            else:
                continue

            for resource in response_resources:
                resource_id = str(resource.resource_id)
                if resource_id not in resources:
                    resources[resource_id] = resource

            for observation in response_observations:
                if isinstance(observation, Mapping):
                    metric_data = observation.get("metric") or {}
                    resource_data = observation.get("resource") or {}
                    metric_name = metric_data.get("name") if isinstance(metric_data, Mapping) else None
                    metric_value = metric_data.get("value") if isinstance(metric_data, Mapping) else None
                    metric_unit = metric_data.get("unit") if isinstance(metric_data, Mapping) else "unknown"
                    metric_labels = metric_data.get("labels") if isinstance(metric_data, Mapping) else {}
                    resource_id = str((resource_data or {}).get("resource_id") or "unknown")
                    resource_name = str((resource_data or {}).get("name") or resource_id)
                    metric_ts = metric_data.get("timestamp") if isinstance(metric_data, Mapping) else None
                    observed_ts = observation.get("observed_at") if isinstance(observation, Mapping) else None
                else:
                    metric = observation.metric
                    resource = observation.resource
                    metric_name = metric.name
                    metric_value = metric.value
                    metric_unit = metric.unit
                    metric_labels = metric.labels
                    resource_id = str(resource.resource_id)
                    resource_name = str(resource.name)
                    metric_ts = metric.timestamp
                    observed_ts = observation.observed_at

                normalized_name = cls._normalize_metric_name(metric_name)
                normalized_unit = cls._normalize_unit(metric_unit)
                normalized_value = cls._normalize_metric_value(metric_value)
                normalized_metric_ts = cls._normalize_timestamp(metric_ts)
                normalized_observed_ts = cls._normalize_timestamp(observed_ts)

                if normalized_value is None:
                    missing_metric_values += 1
                    continue

                signature = cls._metric_signature(
                    normalized_name,
                    normalized_metric_ts or normalized_observed_ts,
                    resource_id,
                    str(metric_labels or {}),
                )
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)

                metrics.append(
                    {
                        "name": normalized_name,
                        "value": normalized_value,
                        "unit": normalized_unit,
                        "timestamp": normalized_metric_ts or normalized_observed_ts or cls._utc_now(),
                        "labels": dict(metric_labels or {}),
                        "resource_id": resource_id,
                        "resource_name": resource_name,
                        "collector": collector_name,
                    }
                )

        metrics.sort(key=lambda item: str(item["timestamp"]))
        snapshot_ts = cls._utc_now()
        if metrics:
            snapshot_ts = max(
                (datetime.fromisoformat(item["timestamp"]) for item in metrics if isinstance(item["timestamp"], str)),
                default=datetime.now(timezone.utc),
            ).isoformat()

        return {
            "resource_count": len(resources),
            "metric_count": len(metrics),
            "timestamp": snapshot_ts,
            "resources": [
                {
                    "resource_id": resource.resource_id,
                    "name": resource.name,
                    "type": resource.type.value if hasattr(resource.type, "value") else str(resource.type),
                    "labels": dict(resource.labels or {}),
                }
                for resource in resources.values()
            ],
            "metrics": metrics,
            "missing_metric_values": missing_metric_values,
        }

    @classmethod
    def _coerce_resource(cls, value: Any) -> Resource:
        if isinstance(value, Resource):
            return value
        if isinstance(value, Mapping):
            return Resource(
                resource_id=str(value.get("resource_id") or value.get("id") or "unknown"),
                name=str(value.get("name") or value.get("resource_id") or value.get("id") or "unknown"),
                type=value.get("type") or "service",
                labels=dict(value.get("labels") or {}),
                metadata=dict(value.get("metadata") or {}),
            )
        return Resource(resource_id="unknown", name="unknown", labels={}, metadata={})

    @classmethod
    def _normalize_metric_name(cls, metric_name: str | Any) -> str:
        value = str(metric_name or "").strip()
        if not value:
            return "unknown_metric"
        lowered = value.lower()
        if lowered in cls._METRIC_ALIASES:
            return cls._METRIC_ALIASES[lowered]
        normalized = lowered.replace(" ", "_")
        normalized = "".join(ch for ch in normalized if ch.isalnum() or ch in {"_", "-"})
        normalized = normalized.replace("-", "_")
        return normalized or "unknown_metric"

    @classmethod
    def _normalize_unit(cls, unit: str | Any) -> str:
        value = str(unit or "").strip()
        if not value:
            return "unknown"
        lowered = value.lower()
        if lowered in cls._UNIT_ALIASES:
            return cls._UNIT_ALIASES[lowered]
        return value

    @classmethod
    def _normalize_metric_value(cls, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _normalize_timestamp(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if stripped.endswith("Z"):
                stripped = stripped[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(stripped)
            except ValueError:
                return None
        else:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()

    @classmethod
    def _metric_signature(cls, name: str, timestamp: str | None, resource_id: str, labels: str) -> str:
        return f"{name}|{timestamp or 'unknown'}|{resource_id}|{labels}"

    @classmethod
    def _utc_now(cls) -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["DataNormalizer"]
