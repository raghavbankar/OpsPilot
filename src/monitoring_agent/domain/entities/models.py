from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

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


__all__ = [
    "CollectorResponse",
    "CollectorStatus",
    "HealthScore",
    "Metric",
    "Observation",
    "Resource",
    "ResourceType",
    "Severity",
]
