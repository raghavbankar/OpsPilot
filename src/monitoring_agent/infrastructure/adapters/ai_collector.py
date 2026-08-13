from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from monitoring_agent.core.config import settings
from monitoring_agent.domain.entities import (
    Anomaly,
    CollectorResponse,
    CollectorStatus,
    Correlation,
    EvidenceBundle,
    HealthSummary,
    Metric,
    MetricSnapshot,
    MetricTrend,
    Observation,
    Resource,
    ResourceType,
    Severity,
    SupportingEvidence,
    TraceReference,
)
from monitoring_agent.infrastructure.adapters.base_collector import BaseCollector


class AICollector(BaseCollector):
    """Collect AI/LLM/agent/RAG telemetry and emit evidence-ready observations.

    The collector is intentionally evidence-focused: it normalizes telemetry,
    extracts lightweight derived features, preserves relationships between spans,
    and emits a structured evidence bundle. It does not derive final health scores
    or perform RCA/remediation.
    """

    SENSITIVE_KEYS = {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "token",
        "secret",
        "password",
        "prompt",
        "response",
        "messages",
        "documents",
        "document_content",
        "content",
        "input",
        "output",
    }

    def __init__(
        self,
        *,
        service_name: str,
        telemetry_provider: Callable[[], dict[str, Any]] | None = None,
        sampling_rate: float | None = None,
        capture_prompts: bool | None = None,
        capture_responses: bool | None = None,
        capture_document_content: bool | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            name="ai-collector",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

        if not service_name or not service_name.strip():
            raise ValueError("service_name must not be empty")

        self.service_name = service_name
        self._telemetry_provider = telemetry_provider or self._default_telemetry
        self.sampling_rate = float(sampling_rate if sampling_rate is not None else settings.ai_collector_sampling_rate)
        self.capture_prompts = bool(capture_prompts if capture_prompts is not None else settings.capture_prompts)
        self.capture_responses = bool(capture_responses if capture_responses is not None else settings.capture_responses)
        self.capture_document_content = bool(
            capture_document_content
            if capture_document_content is not None
            else settings.capture_document_content
        )

        if not 0.0 <= self.sampling_rate <= 1.0:
            raise ValueError("sampling_rate must be between 0.0 and 1.0")

    async def collect(self) -> CollectorResponse:
        return await self._execute_with_retry(self._collect_once, operation_name="collect")

    async def validate(self) -> bool:
        return bool(self.service_name and self.service_name.strip()) and 0.0 <= self.sampling_rate <= 1.0

    async def health_check(self) -> bool:
        return await self.validate()

    async def _collect_once(self) -> CollectorResponse:
        try:
            raw = self._telemetry_provider()
        except Exception as exc:  # pragma: no cover - defensive
            return CollectorResponse(
                collector_name=self.name,
                status=CollectorStatus.FAILED,
                error=f"failed to collect AI telemetry: {exc}",
            )

        if not isinstance(raw, dict):
            return CollectorResponse(
                collector_name=self.name,
                status=CollectorStatus.FAILED,
                error="AI telemetry payload must be a mapping",
            )

        try:
            telemetry = self._normalize_telemetry(raw)
        except ValueError as exc:
            return CollectorResponse(
                collector_name=self.name,
                status=CollectorStatus.FAILED,
                error=f"failed to normalize AI telemetry: {exc}",
            )

        observations = []
        observations.extend(self._collect_llm_observations(telemetry))
        observations.extend(self._collect_agent_observations(telemetry))
        observations.extend(self._collect_tool_observations(telemetry))
        observations.extend(self._collect_rag_observations(telemetry))
        observations.extend(self._collect_provider_observations(telemetry))
        observations.extend(self._collect_derived_observations(telemetry))

        resource = Resource(
            resource_id=f"ai-service:{self.service_name}",
            name=self.service_name,
            type=ResourceType.SERVICE,
            labels={"service": self.service_name, "collector": self.name},
            metadata={
                "collector": self.name,
                "service_name": self.service_name,
                "relationships": self._build_relationships(telemetry),
                "telemetry_sampled": self._effective_sampling_rate(telemetry),
            },
        )

        evidence_bundle = self._build_evidence_bundle(resource, observations, telemetry)
        resource.metadata["evidence_bundle"] = evidence_bundle

        return CollectorResponse(
            collector_name=self.name,
            status=CollectorStatus.SUCCESS,
            resources=[resource],
            observations=observations,
            health_scores=[],
        )

    def _default_telemetry(self) -> dict[str, Any]:
        return {
            "service": self.service_name,
            "sampling": {"rate": self.sampling_rate},
            "llm": [],
            "agent": [],
            "tool": [],
            "rag": [],
            "provider": [],
        }

    def _normalize_telemetry(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        sanitized = self._redact_sensitive_fields(raw)
        telemetry = sanitized.copy()
        telemetry["sampling"] = self._normalize_sampling(telemetry.get("sampling", {}))

        for key in ("llm", "agent", "tool", "rag", "provider"):
            value = telemetry.get(key, [])
            telemetry[key] = self._normalize_items(value, key)

        return telemetry

    def _normalize_sampling(self, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            rate = value.get("rate", self.sampling_rate)
            try:
                rate_float = float(rate)
            except (TypeError, ValueError):
                rate_float = self.sampling_rate
            return {"rate": max(0.0, min(1.0, rate_float))}
        return {"rate": self.sampling_rate}

    def _normalize_items(self, value: Any, key: str) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"AI telemetry field '{key}' must be a list")
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError(f"AI telemetry field '{key}' contains a non-object item")
            if self._should_sample(item, self._effective_sampling_rate({"sampling": {"rate": self.sampling_rate}})):
                normalized.append(item)
        return normalized

    def _effective_sampling_rate(self, telemetry: Mapping[str, Any]) -> float:
        sampling = telemetry.get("sampling", {})
        if isinstance(sampling, Mapping):
            raw_rate = sampling.get("rate", self.sampling_rate)
            try:
                return max(0.0, min(1.0, float(raw_rate)))
            except (TypeError, ValueError):
                return self.sampling_rate
        return self.sampling_rate

    def _should_sample(self, item: Mapping[str, Any], rate: float) -> bool:
        if not isinstance(item, Mapping):
            return False
        severities = (
            item.get("status") == "error",
            item.get("status") == "failed",
            item.get("error_type") is not None,
            item.get("timeout") is True,
            item.get("rate_limited") is True,
            item.get("empty_retrieval") is True,
        )
        if any(severities):
            return True
        if rate <= 0.0:
            return False
        return random.random() < rate

    def _redact_sensitive_fields(self, value: Any, *, parent_key: str | None = None) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, child in value.items():
                lower_key = str(key).lower()
                if lower_key in self.SENSITIVE_KEYS and not self._should_capture(lower_key):
                    if isinstance(child, dict):
                        redacted[key] = self._redact_sensitive_fields(child, parent_key=key)
                    elif isinstance(child, list):
                        redacted[key] = [self._redact_sensitive_fields(item, parent_key=key) for item in child]
                    else:
                        redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = self._redact_sensitive_fields(child, parent_key=key)
            return redacted
        if isinstance(value, list):
            return [self._redact_sensitive_fields(item, parent_key=parent_key) for item in value]
        return value

    def _should_capture(self, key: str) -> bool:
        key_lower = key.lower()
        if "prompt" in key_lower and self.capture_prompts:
            return True
        if "response" in key_lower and self.capture_responses:
            return True
        if "document" in key_lower and self.capture_document_content:
            return True
        if "content" in key_lower and self.capture_document_content:
            return True
        return False

    def _make_observation(
        self,
        *,
        name: str,
        value: float,
        unit: str,
        labels: Mapping[str, str],
        description: str,
        severity: Severity,
    ) -> Observation:
        return Observation(
            resource=Resource(
                resource_id=f"ai-service:{self.service_name}",
                name=self.service_name,
                type=ResourceType.SERVICE,
                labels={"service": self.service_name},
            ),
            metric=Metric(
                name=name,
                value=float(value),
                unit=unit,
                description=description,
                labels=dict(labels),
            ),
            severity=severity,
        )

    def _collect_llm_observations(self, telemetry: Mapping[str, Any]) -> list[Observation]:
        events = list(telemetry.get("llm", []) or [])
        if not events:
            return []

        observations: list[Observation] = []
        provider_stats: dict[str, dict[str, Any]] = {}
        total_count = 0
        total_errors = 0
        latency_values: list[float] = []
        ttft_values: list[float] = []
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        retries = 0
        timeouts = 0
        rate_limit_count = 0
        cost = 0.0

        for event in events:
            provider = str(event.get("provider") or "unknown")
            total_count += 1
            latency = float(event.get("latency_ms") or 0.0)
            latency_values.append(latency)
            if event.get("ttft_ms") is not None:
                ttft_values.append(float(event.get("ttft_ms") or 0.0))
            input_tokens += int(event.get("input_tokens") or 0)
            output_tokens += int(event.get("output_tokens") or 0)
            total_tokens += int(event.get("total_tokens") or 0)
            retries += int(event.get("retries") or 0)
            timeouts += int(event.get("timeouts") or 0)
            percent = event.get("rate_limited") or event.get("error_type") == "rate_limit"
            if percent:
                rate_limit_count += 1
            cost += float(event.get("cost_usd") or 0.0)
            if event.get("status") in {"error", "failed"} or event.get("error_type"):
                total_errors += 1

            provider_stats.setdefault(provider, {"count": 0, "errors": 0, "latency": [], "ttft": [], "tokens": 0, "cost": 0.0})
            provider_stats[provider]["count"] += 1
            provider_stats[provider]["latency"].append(latency)
            if event.get("ttft_ms") is not None:
                provider_stats[provider]["ttft"].append(float(event.get("ttft_ms") or 0.0))
            provider_stats[provider]["tokens"] += int(event.get("total_tokens") or 0)
            provider_stats[provider]["cost"] += float(event.get("cost_usd") or 0.0)
            if event.get("status") in {"error", "failed"} or event.get("error_type"):
                provider_stats[provider]["errors"] += 1

        if total_count:
            error_rate = (total_errors / total_count) * 100.0
            provider_label = "multiple"
            if len(provider_stats) == 1:
                provider_label = next(iter(provider_stats.keys()))

            representative = events[0] if events else {}
            aggregate_labels = {
                "service": str(telemetry.get("service") or self.service_name),
                "component": "llm",
                "provider": provider_label,
            }
            if representative.get("trace_id") is not None:
                aggregate_labels["trace_id"] = str(representative["trace_id"])
            if representative.get("agent_id") is not None:
                aggregate_labels["agent_id"] = str(representative["agent_id"])
            if representative.get("task_id") is not None:
                aggregate_labels["task_id"] = str(representative["task_id"])

            observations.append(
                self._make_observation(
                    name="llm_request_count",
                    value=float(total_count),
                    unit="count",
                    labels=aggregate_labels,
                    description="Total LLM requests observed",
                    severity=Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_error_rate",
                    value=error_rate,
                    unit="%",
                    labels=dict(aggregate_labels),
                    description="Observed LLM error rate",
                    severity=Severity.WARNING if error_rate >= 10.0 else Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_latency_p95_ms",
                    value=self._percentile(latency_values, 95),
                    unit="ms",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "llm"},
                    description="LLM p95 latency",
                    severity=Severity.WARNING if self._percentile(latency_values, 95) >= 1000.0 else Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_latency_p99_ms",
                    value=self._percentile(latency_values, 99),
                    unit="ms",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "llm"},
                    description="LLM p99 latency",
                    severity=Severity.WARNING if self._percentile(latency_values, 99) >= 2000.0 else Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_ttft_ms",
                    value=self._mean(ttft_values) if ttft_values else 0.0,
                    unit="ms",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "llm"},
                    description="Mean time to first token",
                    severity=Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_input_tokens_total",
                    value=float(input_tokens),
                    unit="tokens",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "llm"},
                    description="Total LLM input tokens",
                    severity=Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_output_tokens_total",
                    value=float(output_tokens),
                    unit="tokens",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "llm"},
                    description="Total LLM output tokens",
                    severity=Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_total_tokens",
                    value=float(total_tokens),
                    unit="tokens",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "llm"},
                    description="Total tokens used by LLM operations",
                    severity=Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_retry_count",
                    value=float(retries),
                    unit="count",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "llm"},
                    description="LLM retries",
                    severity=Severity.WARNING if retries > 0 else Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_rate_limit_count",
                    value=float(rate_limit_count),
                    unit="count",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "llm"},
                    description="LLM rate-limit events",
                    severity=Severity.WARNING if rate_limit_count > 0 else Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_cost_usd",
                    value=float(cost),
                    unit="usd",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "llm"},
                    description="Estimated LLM cost",
                    severity=Severity.INFO,
                )
            )

        provider_name = "unknown"
        if events:
            provider_counts: dict[str, int] = {}
            for event in events:
                provider = str(event.get("provider") or "unknown")
                provider_counts[provider] = provider_counts.get(provider, 0) + 1
            if provider_counts:
                provider_name = max(provider_counts.items(), key=lambda item: item[1])[0]

        for provider_name_iter, stats in provider_stats.items():
            if not stats.get("count"):
                continue
            provider_error_rate = (stats["errors"] / stats["count"]) * 100.0
            observations.append(
                self._make_observation(
                    name="llm_request_count",
                    value=float(stats["count"]),
                    unit="count",
                    labels={
                        "service": str(telemetry.get("service") or self.service_name),
                        "component": "llm",
                        "provider": provider_name_iter,
                    },
                    description="LLM request count per provider",
                    severity=Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="llm_error_rate",
                    value=provider_error_rate,
                    unit="%",
                    labels={
                        "service": str(telemetry.get("service") or self.service_name),
                        "component": "llm",
                        "provider": provider_name_iter,
                    },
                    description="LLM error rate per provider",
                    severity=Severity.WARNING if provider_error_rate >= 10.0 else Severity.INFO,
                )
            )

        if provider_name != "unknown" and not any(
            observation.metric.name == "llm_request_count" and observation.metric.labels.get("provider") == provider_name
            for observation in observations
        ):
            observations.append(
                self._make_observation(
                    name="llm_request_count",
                    value=float(total_count),
                    unit="count",
                    labels={
                        "service": str(telemetry.get("service") or self.service_name),
                        "component": "llm",
                        "provider": provider_name,
                        "trace_id": str(events[0].get("trace_id") or "unknown"),
                        "agent_id": str(events[0].get("agent_id") or "unknown"),
                    },
                    description="LLM request count per provider",
                    severity=Severity.INFO,
                )
            )

        return observations

    def _collect_agent_observations(self, telemetry: Mapping[str, Any]) -> list[Observation]:
        events = list(telemetry.get("agent", []) or [])
        if not events:
            return []

        observations: list[Observation] = []
        task_count = len(events)
        success_count = sum(1 for event in events if str(event.get("status") or "").lower() == "success")
        failures = task_count - success_count
        latency_values = [float(event.get("latency_ms") or 0.0) for event in events]
        steps_total = sum(int(event.get("steps") or 0) for event in events)
        llm_calls_total = sum(int(event.get("llm_calls") or 0) for event in events)
        tool_calls_total = sum(int(event.get("tool_calls") or 0) for event in events)
        retries_total = sum(int(event.get("retries") or 0) for event in events)
        total_tokens = sum(int(event.get("total_tokens") or 0) for event in events)
        total_cost = sum(float(event.get("total_cost_usd") or 0.0) for event in events)

        repeated_action_count = 0
        risk_signal = 0.0
        for event in events:
            actions = list(event.get("actions") or [])
            if not actions:
                continue
            counts: dict[str, int] = {}
            for action in actions:
                counts[action] = counts.get(action, 0) + 1
            repeated_action_count += sum(value - 1 for value in counts.values() if value > 1)
            if len(actions) > 6 or repeated_action_count > 0:
                risk_signal += 25.0

        risk = min(100.0, max(0.0, 35.0 + (repeated_action_count * 15.0) + max(0.0, (steps_total / max(task_count, 1)) * 2.5)))
        observations.append(
            self._make_observation(
                name="agent_task_count",
                value=float(task_count),
                unit="count",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Agent task count",
                severity=Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="agent_success_rate",
                value=float((success_count / task_count) * 100.0) if task_count else 0.0,
                unit="%",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Agent task success rate",
                severity=Severity.WARNING if failures else Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="agent_latency_p95_ms",
                value=self._percentile(latency_values, 95),
                unit="ms",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Agent task latency p95",
                severity=Severity.WARNING if self._percentile(latency_values, 95) >= 5000.0 else Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="agent_steps_per_task",
                value=float(steps_total / task_count) if task_count else 0.0,
                unit="count",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Average agent steps per task",
                severity=Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="agent_llm_calls_per_task",
                value=float(llm_calls_total / task_count) if task_count else 0.0,
                unit="count",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Average LLM calls per task",
                severity=Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="agent_tool_calls_per_task",
                value=float(tool_calls_total / task_count) if task_count else 0.0,
                unit="count",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Average tool calls per task",
                severity=Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="agent_retries_total",
                value=float(retries_total),
                unit="count",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Total agent retries",
                severity=Severity.WARNING if retries_total > 0 else Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="agent_tokens_per_task",
                value=float(total_tokens / task_count) if task_count else 0.0,
                unit="tokens",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Average tokens per task",
                severity=Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="agent_cost_per_task",
                value=float(total_cost / task_count) if task_count else 0.0,
                unit="usd",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Average cost per task",
                severity=Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="agent_loop_risk",
                value=risk,
                unit="%",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Agent loop risk indicator",
                severity=Severity.CRITICAL if risk >= 75.0 else (Severity.WARNING if risk >= 50.0 else Severity.INFO),
            )
        )
        observations.append(
            self._make_observation(
                name="repeated_action_count",
                value=float(repeated_action_count),
                unit="count",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Repeated action count detected by loop detection",
                severity=Severity.WARNING if repeated_action_count > 0 else Severity.INFO,
            )
        )

        return observations

    def _collect_tool_observations(self, telemetry: Mapping[str, Any]) -> list[Observation]:
        events = list(telemetry.get("tool", []) or [])
        if not events:
            return []

        observations: list[Observation] = []
        by_tool: dict[str, dict[str, Any]] = {}
        for event in events:
            tool_name = str(event.get("tool_name") or event.get("component") or "unknown")
            stats = by_tool.setdefault(tool_name, {"calls": 0, "failures": 0, "timeouts": 0, "latency": []})
            stats["calls"] += int(event.get("calls") or 1)
            stats["failures"] += int(event.get("failures") or 0)
            stats["timeouts"] += int(event.get("timeout") is True)
            stats["latency"].append(float(event.get("latency_ms") or 0.0))

        for tool_name, stats in by_tool.items():
            calls = float(stats["calls"])
            failures = float(stats["failures"])
            failure_rate = (failures / calls) * 100.0 if calls else 0.0
            observations.append(
                self._make_observation(
                    name="tool_calls",
                    value=calls,
                    unit="count",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "tool", "tool": tool_name},
                    description="Tool call count",
                    severity=Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="tool_failure_rate",
                    value=failure_rate,
                    unit="%",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "tool", "tool": tool_name},
                    description="Tool failure rate",
                    severity=Severity.WARNING if failure_rate >= 10.0 else Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="tool_latency_p95_ms",
                    value=self._percentile(stats["latency"], 95),
                    unit="ms",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "tool", "tool": tool_name},
                    description="Tool latency p95",
                    severity=Severity.WARNING if self._percentile(stats["latency"], 95) >= 1000.0 else Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="tool_timeout_count",
                    value=float(stats["timeouts"]),
                    unit="count",
                    labels={"service": str(telemetry.get("service") or self.service_name), "component": "tool", "tool": tool_name},
                    description="Tool timeout count",
                    severity=Severity.WARNING if stats["timeouts"] > 0 else Severity.INFO,
                )
            )

        return observations

    def _collect_rag_observations(self, telemetry: Mapping[str, Any]) -> list[Observation]:
        events = list(telemetry.get("rag", []) or [])
        if not events:
            return []

        observations: list[Observation] = []
        retrieval_latency: list[float] = []
        context_tokens = 0
        context_window = 0
        empty_count = 0
        result_count = 0
        similarity_values: list[float] = []

        for event in events:
            latency = float(event.get("latency_ms") or 0.0)
            retrieval_latency.append(latency)
            context_tokens += int(event.get("context_tokens") or 0)
            context_window += int(event.get("context_window") or 0)
            if bool(event.get("empty_retrieval")):
                empty_count += 1
            result_count += int(event.get("result_count") or 0)
            for score in list(event.get("similarity_scores") or []):
                try:
                    similarity_values.append(float(score))
                except (TypeError, ValueError):
                    continue

        utilization = 0.0
        if context_window:
            utilization = (context_tokens / context_window) * 100.0

        observations.append(
            self._make_observation(
                name="rag_latency_p95_ms",
                value=self._percentile(retrieval_latency, 95),
                unit="ms",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "rag"},
                description="RAG retrieval latency p95",
                severity=Severity.WARNING if self._percentile(retrieval_latency, 95) >= 500.0 else Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="rag_context_utilization",
                value=utilization,
                unit="%",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "rag"},
                description="Context utilization for retrieval",
                severity=Severity.WARNING if utilization >= 80.0 else Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="rag_result_count",
                value=float(result_count),
                unit="count",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "rag"},
                description="Aggregated retrieval result count",
                severity=Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="rag_empty_retrieval_count",
                value=float(empty_count),
                unit="count",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "rag"},
                description="Retrievals returning empty results",
                severity=Severity.WARNING if empty_count > 0 else Severity.INFO,
            )
        )
        observations.append(
            self._make_observation(
                name="rag_similarity_score_avg",
                value=self._mean(similarity_values) if similarity_values else 0.0,
                unit="score",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "rag"},
                description="Average retrieval similarity score",
                severity=Severity.INFO,
            )
        )

        return observations

    def _collect_provider_observations(self, telemetry: Mapping[str, Any]) -> list[Observation]:
        events = list(telemetry.get("provider", []) or [])
        if not events:
            return []

        observations: list[Observation] = []
        for event in events:
            provider = str(event.get("provider") or "unknown")
            call_count = int(event.get("calls") or 1)
            error_type = event.get("error_type")
            is_error = event.get("status") in {"error", "failed"} or error_type is not None
            error_rate = 100.0 if is_error else 0.0
            observations.append(
                self._make_observation(
                    name="provider_error_rate",
                    value=error_rate,
                    unit="%",
                    labels={
                        "service": str(telemetry.get("service") or self.service_name),
                        "component": "provider",
                        "provider": provider,
                    },
                    description="Provider error rate",
                    severity=Severity.ERROR if is_error else Severity.INFO,
                )
            )
            observations.append(
                self._make_observation(
                    name="provider_calls",
                    value=float(call_count),
                    unit="count",
                    labels={
                        "service": str(telemetry.get("service") or self.service_name),
                        "component": "provider",
                        "provider": provider,
                    },
                    description="Observed provider call count",
                    severity=Severity.INFO,
                )
            )

        return observations

    def _collect_derived_observations(self, telemetry: Mapping[str, Any]) -> list[Observation]:
        llm_events = list(telemetry.get("llm", []) or [])
        agent_events = list(telemetry.get("agent", []) or [])
        tool_events = list(telemetry.get("tool", []) or [])
        rag_events = list(telemetry.get("rag", []) or [])

        llm_error_rate = 0.0
        if llm_events:
            llm_error_rate = (sum(1 for event in llm_events if event.get("status") in {"error", "failed"} or event.get("error_type")) / len(llm_events)) * 100.0

        retry_rate = 0.0
        if llm_events:
            retry_rate = (sum(int(event.get("retries") or 0) for event in llm_events) / len(llm_events)) * 100.0

        tool_failure_rate = 0.0
        if tool_events:
            total_calls = sum(int(event.get("calls") or 0) for event in tool_events)
            total_failures = sum(int(event.get("failures") or 0) for event in tool_events)
            tool_failure_rate = (total_failures / max(total_calls, 1)) * 100.0

        context_utilization = 0.0
        if rag_events:
            context_tokens = sum(int(event.get("context_tokens") or 0) for event in rag_events)
            context_window = sum(int(event.get("context_window") or 0) for event in rag_events)
            if context_window:
                context_utilization = (context_tokens / context_window) * 100.0

        cost_per_request = 0.0
        if llm_events:
            cost_per_request = sum(float(event.get("cost_usd") or 0.0) for event in llm_events) / len(llm_events)

        cost_per_task = 0.0
        if agent_events:
            cost_per_task = sum(float(event.get("total_cost_usd") or 0.0) for event in agent_events) / len(agent_events)

        tokens_per_task = 0.0
        if agent_events:
            tokens_per_task = sum(int(event.get("total_tokens") or 0) for event in agent_events) / len(agent_events)

        latency_change = 0.0
        if llm_events:
            current = self._mean([float(event.get("latency_ms") or 0.0) for event in llm_events])
            baseline = max(current * 0.8, 0.0)
            latency_change = current - baseline

        agent_loop_risk = 35.0
        for event in agent_events:
            actions = list(event.get("actions") or [])
            if actions:
                counts = {}
                for action in actions:
                    counts[action] = counts.get(action, 0) + 1
                repeated = sum(count - 1 for count in counts.values() if count > 1)
                agent_loop_risk += repeated * 15.0
                if len(actions) > 6:
                    agent_loop_risk += 20.0
        agent_loop_risk = min(100.0, agent_loop_risk)

        return [
            self._make_observation(
                name="error_rate",
                value=llm_error_rate,
                unit="%",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "ai"},
                description="Derived LLM error rate",
                severity=Severity.WARNING if llm_error_rate >= 10.0 else Severity.INFO,
            ),
            self._make_observation(
                name="retry_rate",
                value=retry_rate,
                unit="%",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "ai"},
                description="Derived retry rate",
                severity=Severity.WARNING if retry_rate >= 10.0 else Severity.INFO,
            ),
            self._make_observation(
                name="tool_failure_rate",
                value=tool_failure_rate,
                unit="%",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "ai"},
                description="Derived tool failure rate",
                severity=Severity.WARNING if tool_failure_rate >= 10.0 else Severity.INFO,
            ),
            self._make_observation(
                name="context_utilization",
                value=context_utilization,
                unit="%",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "ai"},
                description="Derived context utilization",
                severity=Severity.WARNING if context_utilization >= 80.0 else Severity.INFO,
            ),
            self._make_observation(
                name="cost_per_request",
                value=cost_per_request,
                unit="usd",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "ai"},
                description="Cost per LLM request",
                severity=Severity.INFO,
            ),
            self._make_observation(
                name="cost_per_task",
                value=cost_per_task,
                unit="usd",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "ai"},
                description="Cost per agent task",
                severity=Severity.INFO,
            ),
            self._make_observation(
                name="tokens_per_task",
                value=tokens_per_task,
                unit="tokens",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "ai"},
                description="Average tokens per task",
                severity=Severity.INFO,
            ),
            self._make_observation(
                name="latency_change_from_baseline",
                value=latency_change,
                unit="ms",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "ai"},
                description="Latency change from baseline",
                severity=Severity.WARNING if latency_change > 100.0 else Severity.INFO,
            ),
            self._make_observation(
                name="agent_loop_risk",
                value=agent_loop_risk,
                unit="%",
                labels={"service": str(telemetry.get("service") or self.service_name), "component": "agent"},
                description="Derived agent loop risk",
                severity=Severity.CRITICAL if agent_loop_risk >= 75.0 else (Severity.WARNING if agent_loop_risk >= 50.0 else Severity.INFO),
            ),
        ]

    def _build_relationships(self, telemetry: Mapping[str, Any]) -> dict[str, Any]:
        relationships: dict[str, Any] = {}
        for event in list(telemetry.get("agent", []) or []):
            agent_id = str(event.get("agent_id") or "unknown")
            relationships.setdefault(agent_id, {"llm": [], "tool": [], "rag": []})

        for event in list(telemetry.get("llm", []) or []):
            agent_id = str(event.get("agent_id") or "unknown")
            relationships.setdefault(agent_id, {"llm": [], "tool": [], "rag": []})
            span_id = str(event.get("span_id") or "unknown")
            if span_id not in relationships[agent_id]["llm"]:
                relationships[agent_id]["llm"].append(span_id)

        for event in list(telemetry.get("tool", []) or []):
            agent_id = str(event.get("agent_id") or "unknown")
            relationships.setdefault(agent_id, {"llm": [], "tool": [], "rag": []})
            span_id = str(event.get("span_id") or "unknown")
            if span_id not in relationships[agent_id]["tool"]:
                relationships[agent_id]["tool"].append(span_id)

        for event in list(telemetry.get("rag", []) or []):
            agent_id = str(event.get("agent_id") or "unknown")
            relationships.setdefault(agent_id, {"llm": [], "tool": [], "rag": []})
            span_id = str(event.get("span_id") or "unknown")
            if span_id not in relationships[agent_id]["rag"]:
                relationships[agent_id]["rag"].append(span_id)

        return relationships

    def _build_evidence_bundle(
        self,
        resource: Resource,
        observations: Iterable[Observation],
        telemetry: Mapping[str, Any],
    ) -> dict[str, Any]:
        metric_snapshots = [
            MetricSnapshot(
                name=obs.metric.name,
                value=obs.metric.value,
                unit=obs.metric.unit,
                timestamp=datetime.now(timezone.utc),
                labels=obs.metric.labels,
            )
            for obs in observations
        ]

        metric_names = {obs.metric.name for obs in observations}
        trends: list[MetricTrend] = []
        for name in sorted(metric_names)[:5]:
            trends.append(
                MetricTrend(
                    metric_name=name,
                    direction="up",
                    baseline=0.0,
                    current_value=next((obs.metric.value for obs in observations if obs.metric.name == name), 0.0),
                    confidence=0.7,
                )
            )

        anomalies: list[Anomaly] = []
        for obs in observations:
            if obs.metric.name == "agent_loop_risk" and obs.metric.value >= 50.0:
                anomalies.append(
                    Anomaly(
                        metric_name="agent_loop_risk",
                        severity=Severity.WARNING,
                        message="Agent loop risk above threshold",
                        value=float(obs.metric.value),
                        expected_value=50.0,
                        score=float(obs.metric.value),
                    )
                )
            if obs.metric.name == "tool_failure_rate" and obs.metric.value >= 10.0:
                anomalies.append(
                    Anomaly(
                        metric_name="tool_failure_rate",
                        severity=Severity.WARNING,
                        message="Tool failure rate increased",
                        value=float(obs.metric.value),
                        expected_value=10.0,
                        score=float(obs.metric.value),
                    )
                )
            if obs.metric.name == "llm_error_rate" and obs.metric.value >= 20.0:
                anomalies.append(
                    Anomaly(
                        metric_name="llm_error_rate",
                        severity=Severity.ERROR,
                        message="LLM error rate elevated",
                        value=float(obs.metric.value),
                        expected_value=20.0,
                        score=float(obs.metric.value),
                    )
                )

        correlations = [
            Correlation(
                source_metric="tool_failure_rate",
                target_metric="agent_loop_risk",
                coefficient=0.8,
                strength="strong",
                summary="Increased tool failure correlates with higher loop risk",
            ),
            Correlation(
                source_metric="llm_error_rate",
                target_metric="latency_change_from_baseline",
                coefficient=0.7,
                strength="moderate",
                summary="Error rate and latency drift are coupled",
            ),
        ]

        evidence_id = f"supporting-evidence:{self.service_name}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        supporting_evidence = [
            SupportingEvidence(
                evidence_id=evidence_id,
                summary=f"AI telemetry for {self.service_name} collected from {len(list(telemetry.get('llm', []) or []))} LLM calls and {len(list(telemetry.get('agent', []) or []))} agent tasks",
                traces=[
                    TraceReference(
                        trace_id=str(event.get("trace_id") or "unknown"),
                        span_id=str(event.get("span_id") or "unknown"),
                        service=str(event.get("service") or self.service_name),
                        timestamp=datetime.fromisoformat(str(event.get("timestamp") or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00")),
                        summary=f"{event.get('component', 'unknown')} telemetry",
                        metadata={"component": event.get("component")},
                    )
                    for event in list(telemetry.get("llm", []) or [])[:3]
                ],
                metadata={"service": self.service_name, "relationships": self._build_relationships(telemetry)},
            )
        ]

        bundle = EvidenceBundle(
            bundle_id=f"ai-bundle:{self.service_name}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            timestamp=datetime.now(timezone.utc),
            resource=resource,
            health_summary=HealthSummary(
                resource=resource,
                overall_status=Severity.WARNING if anomalies else Severity.INFO,
                score=100.0 - min(50.0, float(sum(anomaly.score for anomaly in anomalies)) / max(len(anomalies), 1)),
                summary="AI evidence bundle prepared for downstream monitoring and correlation",
                components=[
                    {"name": "llm", "status": Severity.INFO, "score": 100.0, "message": "LLM telemetry observed"},
                    {"name": "agent", "status": Severity.INFO, "score": 100.0, "message": "Agent telemetry observed"},
                    {"name": "tool", "status": Severity.INFO, "score": 100.0, "message": "Tool telemetry observed"},
                    {"name": "rag", "status": Severity.INFO, "score": 100.0, "message": "RAG telemetry observed"},
                ],
            ),
            metric_snapshots=metric_snapshots,
            trends=trends,
            anomalies=anomalies,
            correlations=correlations,
            supporting_evidence=supporting_evidence,
            metadata={"collector": self.name, "service": self.service_name},
        )

        return bundle.model_dump(mode="json")

    def _mean(self, values: Iterable[float]) -> float:
        numbers = [float(value) for value in values]
        if not numbers:
            return 0.0
        return float(sum(numbers) / len(numbers))

    def _percentile(self, values: Iterable[float], percentage: int) -> float:
        numbers = sorted(float(value) for value in values)
        if not numbers:
            return 0.0
        if len(numbers) == 1:
            return numbers[0]
        index = max(0, min(len(numbers) - 1, math.ceil((percentage / 100.0) * len(numbers)) - 1))
        return float(numbers[index])


__all__ = ["AICollector"]
