from monitoring_agent.domain.entities import (
    CollectorResponse,
    CollectorStatus,
    HealthScore,
    Metric,
    Observation,
    Resource,
    ResourceType,
    Severity,
)


def test_models_validate_and_expose_expected_fields() -> None:
    resource = Resource(
        resource_id="svc-1",
        name="checkout-api",
        type=ResourceType.API,
        labels={"env": "prod"},
    )

    metric = Metric(name="latency_ms", value=120.5, unit="ms")
    observation = Observation(resource=resource, metric=metric, severity=Severity.WARNING)
    health = HealthScore(resource=resource, score=89.0, severity=Severity.INFO)

    response = CollectorResponse(
        collector_name="prometheus",
        status=CollectorStatus.SUCCESS,
        resources=[resource],
        observations=[observation],
        health_scores=[health],
    )

    assert response.collector_name == "prometheus"
    assert response.status is CollectorStatus.SUCCESS
    assert response.resources[0].type is ResourceType.API
    assert observation.severity is Severity.WARNING
    assert health.score == 89.0
