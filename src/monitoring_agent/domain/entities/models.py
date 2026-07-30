from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MonitoringBaseModel(BaseModel):
    """Shared base model with production-friendly defaults for the monitoring domain."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        populate_by_name=True,
    )


class Severity(str, Enum):
    """Standardized severity levels for observations and health events."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ResourceType(str, Enum):
    """Supported monitored resource categories."""

    API = "api"
    SERVICE = "service"
    DATABASE = "database"
    QUEUE = "queue"
    HOST = "host"
    WORKER = "worker"
    UNKNOWN = "unknown"


class CollectorStatus(str, Enum):
    """Lifecycle status for a collector run."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class Metric(MonitoringBaseModel):
    """A single numeric metric captured for a monitored resource."""

    name: str = Field(..., min_length=1, max_length=128)
    value: float = Field(..., description="Numeric value of the metric")
    unit: str = Field(default="unknown", min_length=1, max_length=32)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    description: str | None = Field(default=None, max_length=500)
    labels: dict[str, str] = Field(default_factory=dict)


class Resource(MonitoringBaseModel):
    """A monitored resource such as a service, database, queue, or host."""

    resource_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    type: ResourceType = Field(default=ResourceType.UNKNOWN)
    labels: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = Field(default=None)


class Observation(MonitoringBaseModel):
    """A single observation derived from an observed metric and resource."""

    resource: Resource
    metric: Metric
    severity: Severity = Field(default=Severity.INFO)
    message: str | None = Field(default=None, max_length=1000)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HealthScore(MonitoringBaseModel):
    """Aggregated health information for a resource."""

    resource: Resource
    score: float = Field(..., ge=0, le=100)
    severity: Severity = Field(default=Severity.INFO)
    summary: str | None = Field(default=None, max_length=500)
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CollectorResponse(MonitoringBaseModel):
    """Response payload returned by a collector after a full run."""

    collector_name: str = Field(..., min_length=1, max_length=128)
    status: CollectorStatus
    resources: list[Resource] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    health_scores: list[HealthScore] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = Field(default=None)
    duration_ms: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_timing(self) -> "CollectorResponse":
        if self.completed_at is not None and self.started_at > self.completed_at:
            raise ValueError("completed_at must be greater than or equal to started_at")
        return self


class HealthSummary(MonitoringBaseModel):
    """Aggregated health summary for a monitored resource."""

    resource: Resource
    overall_status: Severity = Field(default=Severity.INFO)
    score: float = Field(..., ge=0, le=100)
    summary: str | None = Field(default=None, max_length=1000)
    components: list["ComponentHealth"] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ComponentHealth(MonitoringBaseModel):
    """Health state for an individual component within a resource."""

    name: str = Field(..., min_length=1, max_length=128)
    status: Severity = Field(default=Severity.INFO)
    score: float = Field(..., ge=0, le=100)
    message: str | None = Field(default=None, max_length=500)


class MetricSnapshot(MonitoringBaseModel):
    """A point-in-time metric sample captured for evidence gathering."""

    name: str = Field(..., min_length=1, max_length=128)
    value: float = Field(..., description="Numeric value of the metric")
    unit: str = Field(default="unknown", min_length=1, max_length=32)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    labels: dict[str, str] = Field(default_factory=dict)


class MetricTrend(MonitoringBaseModel):
    """Trend information derived from metric history."""

    metric_name: str = Field(..., min_length=1, max_length=128)
    direction: Literal["up", "down", "stable"] = Field(default="stable")
    slope: float | None = Field(default=None)
    baseline: float | None = Field(default=None)
    current_value: float | None = Field(default=None)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Anomaly(MonitoringBaseModel):
    """A detected anomaly that may indicate an incident or reliability issue."""

    metric_name: str = Field(..., min_length=1, max_length=128)
    severity: Severity = Field(default=Severity.WARNING)
    message: str = Field(..., min_length=1, max_length=1000)
    value: float = Field(...)
    expected_value: float | None = Field(default=None)
    score: float = Field(..., ge=0, le=100)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Correlation(MonitoringBaseModel):
    """Correlation between two metrics or signals."""

    source_metric: str = Field(..., min_length=1, max_length=128)
    target_metric: str = Field(..., min_length=1, max_length=128)
    coefficient: float = Field(..., ge=-1, le=1)
    strength: Literal["weak", "moderate", "strong"] = Field(default="moderate")
    summary: str | None = Field(default=None, max_length=500)


class LogReference(MonitoringBaseModel):
    """Reference to a log entry that supports an investigation."""

    log_id: str = Field(..., min_length=1, max_length=128)
    source: str = Field(..., min_length=1, max_length=256)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    level: str = Field(default="info", min_length=1, max_length=32)
    message: str = Field(..., min_length=1, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceReference(MonitoringBaseModel):
    """Reference to a trace span or distributed trace."""

    trace_id: str = Field(..., min_length=1, max_length=128)
    span_id: str | None = Field(default=None, min_length=1, max_length=128)
    service: str = Field(..., min_length=1, max_length=256)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KubernetesEventReference(MonitoringBaseModel):
    """Reference to a Kubernetes event relevant to an incident."""

    event_id: str = Field(..., min_length=1, max_length=128)
    namespace: str = Field(..., min_length=1, max_length=256)
    kind: str = Field(..., min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=256)
    message: str = Field(..., min_length=1, max_length=2000)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeploymentEventReference(MonitoringBaseModel):
    """Reference to a deployment event relevant to observed health changes."""

    deployment_id: str = Field(..., min_length=1, max_length=128)
    environment: str = Field(..., min_length=1, max_length=128)
    status: str = Field(..., min_length=1, max_length=64)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SupportingEvidence(MonitoringBaseModel):
    """A container for supporting evidence references tied to a bundle."""

    evidence_id: str = Field(..., min_length=1, max_length=128)
    summary: str = Field(..., min_length=1, max_length=1000)
    logs: list[LogReference] = Field(default_factory=list)
    traces: list[TraceReference] = Field(default_factory=list)
    kubernetes_events: list[KubernetesEventReference] = Field(default_factory=list)
    deployment_events: list[DeploymentEventReference] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(MonitoringBaseModel):
    """A structured evidence bundle describing health, metrics, anomalies, and supporting context."""

    bundle_id: str = Field(..., min_length=1, max_length=128)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resource: Resource
    health_summary: HealthSummary
    metric_snapshots: list[MetricSnapshot] = Field(default_factory=list)
    trends: list[MetricTrend] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)
    correlations: list[Correlation] = Field(default_factory=list)
    supporting_evidence: list[SupportingEvidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "Anomaly",
    "CollectorResponse",
    "CollectorStatus",
    "ComponentHealth",
    "Correlation",
    "DeploymentEventReference",
    "EvidenceBundle",
    "HealthScore",
    "HealthSummary",
    "KubernetesEventReference",
    "LogReference",
    "Metric",
    "MetricSnapshot",
    "MetricTrend",
    "Observation",
    "Resource",
    "ResourceType",
    "Severity",
    "SupportingEvidence",
    "TraceReference",
]
