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


class ContainerCollector(BaseCollector):
    """Collect container (Docker-like) telemetry using mock data.

    Designed to be async, return standardized models, and be easily replaced
    with a real Docker SDK integration later.
    """

    def __init__(
        self,
        *,
        host_id: str,
        mock_docker_provider: Callable[[], dict] | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            name="container-collector",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

        if not host_id or not host_id.strip():
            raise ValueError("host_id must not be empty")

        self.host_id = host_id
        self._mock_docker_provider = mock_docker_provider or self._default_mock_docker_data

    async def collect(self) -> CollectorResponse:
        return await self._execute_with_retry(self._collect_once, operation_name="collect")

    async def validate(self) -> bool:
        return bool(self.host_id and self.host_id.strip())

    async def health_check(self) -> bool:
        return await self.validate()

    async def _collect_once(self) -> CollectorResponse:
        try:
            raw = self._mock_docker_provider()
        except Exception as exc:  # pragma: no cover - defensive
            return CollectorResponse(
                collector_name=self.name,
                status=CollectorStatus.FAILED,
                error=f"failed to collect container telemetry: {exc}",
            )

        try:
            containers = self._normalize_list(raw.get("containers", []))
        except Exception as exc:  # pragma: no cover - defensive
            return CollectorResponse(
                collector_name=self.name,
                status=CollectorStatus.FAILED,
                error=f"failed to normalize container telemetry: {exc}",
            )

        resources: list[Resource] = []
        observations: list[Observation] = []

        state_map = {"running": 1.0, "exited": 0.0, "paused": 2.0}

        for c in containers:
            cid = c.get("id") or c.get("name") or "unknown"
            resource_id = f"container:{cid}"
            resource = Resource(
                resource_id=resource_id,
                name=c.get("name", resource_id),
                type=ResourceType.WORKER,
                labels={
                    "host_id": self.host_id,
                    "container_id": cid,
                    "image": c.get("image", ""),
                },
                metadata={"docker": True},
            )
            resources.append(resource)

            # container state (encoded numeric)
            state_raw = str(c.get("state", "unknown")).lower()
            state_value = state_map.get(state_raw, -1.0)
            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name="container_state",
                        value=float(state_value),
                        unit="state_code",
                        description="Encoded container state (running=1, exited=0, paused=2)",
                        labels={"state": state_raw},
                    ),
                    severity=Severity.INFO,
                )
            )

            # CPU
            if "cpu_percent" in c:
                cpu = float(c.get("cpu_percent", 0.0))
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_cpu_percent",
                            value=cpu,
                            unit="%",
                            description="Container CPU percent",
                            labels={"container": cid},
                        ),
                        severity=Severity.WARNING if cpu >= 80.0 else Severity.INFO,
                    )
                )

            # Memory
            if "memory_percent" in c:
                mem = float(c.get("memory_percent", 0.0))
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_memory_percent",
                            value=mem,
                            unit="%",
                            description="Container memory percent",
                            labels={"container": cid},
                        ),
                        severity=Severity.WARNING if mem >= 80.0 else Severity.INFO,
                    )
                )

            # Restarts
            restarts = float(c.get("restarts", 0))
            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name="container_restart_count",
                        value=restarts,
                        unit="count",
                        description="Container restart count",
                        labels={"container": cid},
                    ),
                    severity=Severity.WARNING if restarts > 0 else Severity.INFO,
                )
            )

            # Image version as a labeled metric
            image = str(c.get("image", ""))
            if image:
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_image_present",
                            value=1.0,
                            unit="count",
                            description="Indicator that image is present",
                            labels={"image": image},
                        ),
                        severity=Severity.INFO,
                    )
                )

            # Network (bytes)
            if "network_rx_bytes" in c:
                rx = float(c.get("network_rx_bytes", 0.0))
                tx = float(c.get("network_tx_bytes", 0.0))
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_network_rx_bytes",
                            value=rx,
                            unit="bytes",
                            description="Container network received bytes",
                            labels={"container": cid},
                        ),
                        severity=Severity.INFO,
                    )
                )
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_network_tx_bytes",
                            value=tx,
                            unit="bytes",
                            description="Container network transmitted bytes",
                            labels={"container": cid},
                        ),
                        severity=Severity.INFO,
                    )
                )

            # Disk I/O
            if "disk_read_bytes" in c:
                read_b = float(c.get("disk_read_bytes", 0.0))
                write_b = float(c.get("disk_write_bytes", 0.0))
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_disk_read_bytes",
                            value=read_b,
                            unit="bytes",
                            description="Container disk read bytes",
                            labels={"container": cid},
                        ),
                        severity=Severity.INFO,
                    )
                )
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_disk_write_bytes",
                            value=write_b,
                            unit="bytes",
                            description="Container disk write bytes",
                            labels={"container": cid},
                        ),
                        severity=Severity.INFO,
                    )
                )

            # Exit code
            if "exit_code" in c:
                exit_code = float(c.get("exit_code", 0.0))
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_exit_code",
                            value=exit_code,
                            unit="code",
                            description="Container last exit code",
                            labels={"container": cid},
                        ),
                        severity=Severity.ERROR if exit_code != 0 else Severity.INFO,
                    )
                )

            # Uptime
            if "uptime_seconds" in c:
                uptime = float(c.get("uptime_seconds", 0.0))
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_uptime_seconds",
                            value=uptime,
                            unit="seconds",
                            description="Container uptime in seconds",
                            labels={"container": cid},
                        ),
                        severity=Severity.INFO,
                    )
                )

            # Health checks and probe failures
            health = str(c.get("health_status", "unknown")).lower()
            if health:
                hs_val = 1.0 if health == "healthy" else 0.0
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="container_health_status",
                            value=hs_val,
                            unit="boolean",
                            description="Health check status (healthy=1.0)",
                            labels={"status": health},
                        ),
                        severity=Severity.ERROR if hs_val == 0.0 else Severity.INFO,
                    )
                )

            liveness = float(c.get("liveness_probe_failures", 0))
            readiness = float(c.get("readiness_probe_failures", 0))
            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name="container_liveness_probe_failures",
                        value=liveness,
                        unit="count",
                        description="Number of liveness probe failures",
                        labels={"container": cid},
                    ),
                    severity=Severity.WARNING if liveness > 0 else Severity.INFO,
                )
            )
            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name="container_readiness_probe_failures",
                        value=readiness,
                        unit="count",
                        description="Number of readiness probe failures",
                        labels={"container": cid},
                    ),
                    severity=Severity.WARNING if readiness > 0 else Severity.INFO,
                )
            )

        return CollectorResponse(
            collector_name=self.name,
            status=CollectorStatus.SUCCESS,
            resources=resources,
            observations=observations,
            health_scores=[],
        )

    def _normalize_list(self, value: Any) -> list[dict]:
        if value is None:
            return []
        if isinstance(value, list):
            return [dict(item) if isinstance(item, Mapping) else item for item in value]
        raise ValueError("expected a list for container collection field")

    def _default_mock_docker_data(self) -> dict:
        return {
            "containers": [
                {
                    "id": "abc123",
                    "name": "web",
                    "image": "nginx:1.21",
                    "state": "running",
                    "cpu_percent": 12.5,
                    "memory_percent": 34.2,
                    "restarts": 0,
                    "network_rx_bytes": 102400,
                    "network_tx_bytes": 204800,
                    "disk_read_bytes": 4096,
                    "disk_write_bytes": 8192,
                    "exit_code": 0,
                    "uptime_seconds": 3600,
                    "health_status": "healthy",
                    "liveness_probe_failures": 0,
                    "readiness_probe_failures": 0,
                },
                {
                    "id": "def456",
                    "name": "worker",
                    "image": "worker:2.3.1",
                    "state": "running",
                    "cpu_percent": 82.1,
                    "memory_percent": 78.6,
                    "restarts": 2,
                    "network_rx_bytes": 51200,
                    "network_tx_bytes": 102400,
                    "disk_read_bytes": 16384,
                    "disk_write_bytes": 32768,
                    "exit_code": 1,
                    "uptime_seconds": 120,
                    "health_status": "unhealthy",
                    "liveness_probe_failures": 1,
                    "readiness_probe_failures": 2,
                },
            ]
        }
