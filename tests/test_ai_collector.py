import pytest

from monitoring_agent.domain.entities import CollectorStatus, Severity
from monitoring_agent.infrastructure.adapters import AICollector


def _base_payload() -> dict:
    return {
        "service": "ops-pilot",
        "component": "ai-orchestrator",
        "trace_id": "trace-001",
        "span_id": "span-001",
        "parent_span_id": "root",
        "llm": [
            {
                "trace_id": "trace-001",
                "span_id": "llm-span-1",
                "parent_span_id": "span-001",
                "timestamp": "2026-08-13T00:00:00Z",
                "service": "ops-pilot",
                "component": "llm",
                "agent_id": "agent-1",
                "task_id": "task-1",
                "model": "gpt-4o-mini",
                "provider": "openai",
                "status": "success",
                "latency_ms": 420,
                "ttft_ms": 110,
                "input_tokens": 1200,
                "output_tokens": 300,
                "total_tokens": 1500,
                "retries": 1,
                "timeouts": 0,
                "rate_limited": False,
                "cost_usd": 0.015,
                "context_window": 12000,
                "context_tokens": 8000,
            },
            {
                "trace_id": "trace-001",
                "span_id": "llm-span-2",
                "parent_span_id": "span-001",
                "timestamp": "2026-08-13T00:00:01Z",
                "service": "ops-pilot",
                "component": "llm",
                "agent_id": "agent-1",
                "task_id": "task-1",
                "model": "gpt-4o-mini",
                "provider": "openai",
                "status": "error",
                "error_type": "rate_limit",
                "latency_ms": 980,
                "ttft_ms": 220,
                "input_tokens": 1800,
                "output_tokens": 0,
                "total_tokens": 1800,
                "retries": 3,
                "timeouts": 0,
                "rate_limited": True,
                "cost_usd": 0.025,
                "context_window": 12000,
                "context_tokens": 9000,
            },
        ],
        "agent": [
            {
                "agent_id": "agent-1",
                "task_id": "task-1",
                "status": "success",
                "latency_ms": 4200,
                "steps": 12,
                "llm_calls": 2,
                "tool_calls": 4,
                "retries": 2,
                "total_tokens": 3300,
                "total_cost_usd": 0.04,
                "actions": ["search", "search", "search", "lookup", "lookup"],
                "arguments": [{"q": "alpha"}, {"q": "alpha"}, {"q": "alpha"}],
            }
        ],
        "tool": [
            {
                "trace_id": "trace-001",
                "span_id": "tool-span-1",
                "parent_span_id": "span-001",
                "timestamp": "2026-08-13T00:00:00Z",
                "service": "ops-pilot",
                "component": "tool",
                "tool_name": "search_docs",
                "agent_id": "agent-1",
                "task_id": "task-1",
                "provider": "internal",
                "status": "success",
                "latency_ms": 200,
                "retries": 0,
                "timeout": False,
                "calls": 3,
                "failures": 1,
            },
            {
                "trace_id": "trace-001",
                "span_id": "tool-span-2",
                "parent_span_id": "span-001",
                "timestamp": "2026-08-13T00:00:02Z",
                "service": "ops-pilot",
                "component": "tool",
                "tool_name": "lookup_ticket",
                "agent_id": "agent-1",
                "task_id": "task-1",
                "provider": "internal",
                "status": "error",
                "latency_ms": 600,
                "retries": 2,
                "timeout": True,
                "calls": 2,
                "failures": 2,
            },
        ],
        "rag": [
            {
                "trace_id": "trace-001",
                "span_id": "rag-span-1",
                "parent_span_id": "span-001",
                "timestamp": "2026-08-13T00:00:00Z",
                "service": "ops-pilot",
                "component": "retrieval",
                "agent_id": "agent-1",
                "task_id": "task-1",
                "provider": "vector-db",
                "kind": "retrieval",
                "latency_ms": 220,
                "top_k": 5,
                "result_count": 4,
                "similarity_scores": [0.79, 0.82, 0.68, 0.91],
                "empty_retrieval": False,
                "context_tokens": 2800,
                "context_window": 8192,
            }
        ],
        "provider": [
            {
                "provider": "openai",
                "status": "error",
                "error_type": "rate_limit",
                "latency_ms": 500,
                "calls": 2,
            }
        ],
        "sampling": {"rate": 1.0},
    }


@pytest.mark.asyncio
async def test_ai_collector_collects_llm_and_agent_telemetry() -> None:
    collector = AICollector(service_name="ops-pilot", telemetry_provider=lambda: _base_payload())

    response = await collector.collect()

    assert response.status is CollectorStatus.SUCCESS
    assert response.collector_name == "ai-collector"
    assert response.resources

    metric_names = {observation.metric.name for observation in response.observations}
    assert "llm_request_count" in metric_names
    assert "llm_error_rate" in metric_names
    assert "agent_task_count" in metric_names
    assert "agent_loop_risk" in metric_names
    assert "tool_failure_rate" in metric_names
    assert "rag_context_utilization" in metric_names

    llm_observation = next(observation for observation in response.observations if observation.metric.name == "llm_request_count")
    assert llm_observation.metric.value == 2.0
    assert llm_observation.metric.labels["provider"] == "openai"
    assert llm_observation.metric.labels["trace_id"] == "trace-001"
    assert llm_observation.metric.labels["agent_id"] == "agent-1"

    loop_observation = next(observation for observation in response.observations if observation.metric.name == "agent_loop_risk")
    assert loop_observation.metric.value >= 50.0
    assert loop_observation.severity in {Severity.WARNING, Severity.CRITICAL}


@pytest.mark.asyncio
async def test_ai_collector_builds_evidence_bundle_and_relationships() -> None:
    collector = AICollector(service_name="ops-pilot", telemetry_provider=lambda: _base_payload())

    response = await collector.collect()
    resource = response.resources[0]
    evidence_bundle = resource.metadata.get("evidence_bundle")

    assert evidence_bundle is not None
    assert evidence_bundle["resource"]["resource_id"] == "ai-service:ops-pilot"
    assert evidence_bundle["metric_snapshots"]
    assert evidence_bundle["trends"]
    assert evidence_bundle["anomalies"]
    assert resource.metadata.get("relationships")


@pytest.mark.asyncio
async def test_ai_collector_redacts_sensitive_payloads() -> None:
    payload = {
        "service": "ops-pilot",
        "llm": [{
            "trace_id": "trace-1",
            "span_id": "span-1",
            "parent_span_id": "root",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "prompt": "sk-proj-secret-123 should not be captured",
            "response": {"content": "super secret value"},
            "latency_ms": 100,
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
            "cost_usd": 0.01,
            "agent_id": "agent-1",
            "task_id": "task-1",
            "timestamp": "2026-08-13T00:00:00Z",
        }],
        "sampling": {"rate": 1.0},
    }
    collector = AICollector(service_name="ops-pilot", telemetry_provider=lambda: payload)

    sanitized = collector._redact_sensitive_fields(payload)

    assert "sk-proj-secret-123" not in str(sanitized)
    assert "secret value" not in str(sanitized)
    assert sanitized["llm"][0]["prompt"] == "[REDACTED]"
    assert sanitized["llm"][0]["response"] == {"content": "[REDACTED]"}


@pytest.mark.asyncio
async def test_ai_collector_handles_provider_errors_and_sampling() -> None:
    payload = {
        "service": "ops-pilot",
        "provider": [{
            "provider": "anthropic",
            "status": "error",
            "error_type": "provider_unavailable",
            "latency_ms": 900,
            "calls": 3,
        }],
        "sampling": {"rate": 0.0},
    }
    collector = AICollector(service_name="ops-pilot", telemetry_provider=lambda: payload)

    response = await collector.collect()

    assert response.status is CollectorStatus.SUCCESS
    provider_obs = next((observation for observation in response.observations if observation.metric.name == "provider_error_rate"), None)
    assert provider_obs is not None
    assert provider_obs.metric.labels["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_ai_collector_handles_malformed_telemetry_gracefully() -> None:
    collector = AICollector(service_name="ops-pilot", telemetry_provider=lambda: {"llm": "not-a-list"})

    response = await collector.collect()

    assert response.status is CollectorStatus.FAILED
    assert response.error is not None


@pytest.mark.asyncio
async def test_ai_collector_handles_collector_failure() -> None:
    def boom() -> dict:
        raise RuntimeError("telemetry backend down")

    collector = AICollector(service_name="ops-pilot", telemetry_provider=boom)

    response = await collector.collect()

    assert response.status is CollectorStatus.FAILED
    assert "telemetry backend down" in response.error
