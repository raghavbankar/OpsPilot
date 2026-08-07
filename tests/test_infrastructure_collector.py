import pytest

from monitoring_agent.domain.entities import CollectorStatus, ResourceType, Severity
from monitoring_agent.infrastructure.adapters import InfrastructureCollector


@pytest.mark.asyncio
async def test_infrastructure_collector_collects_mock_telemetry() -> None:
    collector = InfrastructureCollector(
        host_id="host-1",
        thresholds={
            "cpu_percent": 80.0,
            "memory_percent": 85.0,
            "disk_percent": 90.0,
            "network_rx_mbps": 100.0,
            "process_count": 200.0,
        },
    )

    response = await collector.collect()

    assert response.status is CollectorStatus.SUCCESS
    assert response.collector_name == "infrastructure-collector"
    assert response.resources
    assert len(response.observations) == 5
    assert response.health_scores == []
    assert any(observation.resource.type is ResourceType.HOST for observation in response.observations)

    metric_names = {observation.metric.name for observation in response.observations}
    assert metric_names == {
        "cpu_percent",
        "memory_percent",
        "disk_percent",
        "network_rx_mbps",
        "process_count",
    }

    cpu_observation = next(observation for observation in response.observations if observation.metric.name == "cpu_percent")
    assert cpu_observation.metric.value > 0
    assert cpu_observation.severity is Severity.INFO


@pytest.mark.asyncio
async def test_infrastructure_collector_uses_thresholds_for_severity() -> None:
    collector = InfrastructureCollector(host_id="host-2", thresholds={"cpu_percent": 50.0})

    response = await collector.collect()

    cpu_observation = next(observation for observation in response.observations if observation.metric.name == "cpu_percent")
    assert cpu_observation.severity is Severity.WARNING


@pytest.mark.asyncio
async def test_infrastructure_collector_handles_failures_gracefully() -> None:
    def failing_provider() -> dict[str, float]:
        raise RuntimeError("mock provider exploded")

    collector = InfrastructureCollector(host_id="host-3", mock_data_provider=failing_provider)

    response = await collector.collect()

    assert response.status is CollectorStatus.FAILED
    assert response.error is not None
    assert response.observations == []
    assert response.resources == []
