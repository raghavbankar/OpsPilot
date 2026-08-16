from datetime import datetime, timezone

from monitoring_agent.domain.entities import CollectorResponse, CollectorStatus, Metric, Observation, Resource, ResourceType
from monitoring_agent.domain.services import DataNormalizer


def test_data_normalizer_merges_and_deduplicates_metrics() -> None:
    response_a = CollectorResponse(
        collector_name="application-collector",
        status=CollectorStatus.SUCCESS,
        started_at=datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 13, 8, 0, 5, tzinfo=timezone.utc),
        resources=[Resource(resource_id="svc:orders", name="orders", type=ResourceType.SERVICE)],
        observations=[
            Observation(
                resource=Resource(resource_id="svc:orders", name="orders", type=ResourceType.SERVICE),
                metric=Metric(
                    name="Latency p95",
                    value=320.5,
                    unit="ms",
                    timestamp=datetime(2026, 8, 13, 8, 0, 0, tzinfo=timezone.utc),
                    labels={"endpoint": "/orders"},
                ),
                observed_at=datetime(2026, 8, 13, 8, 0, 0, tzinfo=timezone.utc),
            ),
            Observation(
                resource=Resource(resource_id="svc:orders", name="orders", type=ResourceType.SERVICE),
                metric=Metric(
                    name="Error Rate",
                    value=2.5,
                    unit="percent",
                    timestamp=datetime(2026, 8, 13, 8, 0, 0, tzinfo=timezone.utc),
                    labels={"endpoint": "/orders"},
                ),
                observed_at=datetime(2026, 8, 13, 8, 0, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    response_b = {
        "collector_name": "infrastructure-collector",
        "resources": [{"resource_id": "svc:orders", "name": "orders", "type": "service"}],
        "observations": [
            {
                "resource": {"resource_id": "svc:orders", "name": "orders", "type": "service"},
                "metric": {
                    "name": "latency_p95",
                    "value": 320.5,
                    "unit": "ms",
                    "timestamp": "2026-08-13T08:00:00Z",
                    "labels": {"endpoint": "/orders"},
                },
                "observed_at": "2026-08-13T08:00:00Z",
            },
            {
                "resource": {"resource_id": "svc:orders", "name": "orders", "type": "service"},
                "metric": {
                    "name": "requests_total",
                    "value": None,
                    "unit": "count",
                    "timestamp": "2026-08-13T08:00:02Z",
                    "labels": {"endpoint": "/orders"},
                },
                "observed_at": "2026-08-13T08:00:02Z",
            },
        ],
    }

    snapshot = DataNormalizer.normalize([response_a, response_b])

    assert snapshot["resource_count"] == 1
    assert snapshot["metric_count"] == 2
    assert {metric["name"] for metric in snapshot["metrics"]} == {"latency_p95", "error_rate"}
    assert all(metric["unit"] in {"ms", "%"} for metric in snapshot["metrics"])
    assert snapshot["timestamp"]
    assert snapshot["metrics"][0]["timestamp"]
    assert snapshot["metrics"][0]["value"] == 320.5


def test_data_normalizer_handles_missing_values_and_missing_timestamps() -> None:
    response = {
        "collector_name": "ai-collector",
        "resources": [{"resource_id": "svc:ai", "name": "ai", "type": "service"}],
        "observations": [
            {
                "resource": {"resource_id": "svc:ai", "name": "ai", "type": "service"},
                "metric": {"name": "tokens_per_request", "value": None, "unit": "tokens", "labels": {}},
                "observed_at": None,
            }
        ],
    }

    snapshot = DataNormalizer.normalize([response])

    assert snapshot["metric_count"] == 0
    assert snapshot["missing_metric_values"] == 1
