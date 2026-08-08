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


class KubernetesCollector(BaseCollector):
    """Collect only Kubernetes telemetry using mock data and emit standardized observations.

    The implementation uses a mock provider that can be replaced with a real
    Kubernetes API client later. The collector is async, uses the base retry
    facilities, and returns `CollectorResponse` objects composed of
    `Resource` and `Observation` models.
    """

    def __init__(
        self,
        *,
        cluster_name: str,
        mock_k8s_provider: Callable[[], dict] | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            name="kubernetes-collector",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

        if not cluster_name or not cluster_name.strip():
            raise ValueError("cluster_name must not be empty")

        self.cluster_name = cluster_name
        self._mock_k8s_provider = mock_k8s_provider or self._default_mock_k8s_data

    async def collect(self) -> CollectorResponse:
        return await self._execute_with_retry(self._collect_once, operation_name="collect")

    async def validate(self) -> bool:
        return bool(self.cluster_name and self.cluster_name.strip())

    async def health_check(self) -> bool:
        return await self.validate()

    async def _collect_once(self) -> CollectorResponse:
        try:
            raw = self._mock_k8s_provider()
        except Exception as exc:  # pragma: no cover - defensive
            return CollectorResponse(
                collector_name=self.name,
                status=CollectorStatus.FAILED,
                error=f"failed to collect kubernetes telemetry: {exc}",
            )

        try:
            pods = self._normalize_list(raw.get("pods", []))
            deployments = self._normalize_list(raw.get("deployments", []))
            nodes = self._normalize_list(raw.get("nodes", []))
            events = self._normalize_list(raw.get("events", []))
            namespaces = list(raw.get("namespaces", []))
        except Exception as exc:  # pragma: no cover - defensive
            return CollectorResponse(
                collector_name=self.name,
                status=CollectorStatus.FAILED,
                error=f"failed to normalize kubernetes telemetry: {exc}",
            )

        resources: list[Resource] = []
        observations: list[Observation] = []

        # Nodes
        for node in nodes:
            node_id = f"node:{node.get('name','unknown')}"
            resource = Resource(
                resource_id=node_id,
                name=node.get("name", node_id),
                type=ResourceType.HOST,
                labels={"cluster": self.cluster_name, "node": node.get("name", "")},
                metadata={"kubernetes": True},
            )
            resources.append(resource)

            if "cpu_percent" in node:
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="node_cpu_percent",
                            value=float(node.get("cpu_percent", 0.0)),
                            unit="%",
                            description="Node CPU utilization",
                            labels={"node": node.get("name", "")},
                        ),
                        severity=self._severity_for_percent(node.get("cpu_percent", 0.0)),
                    )
                )

            if "memory_percent" in node:
                observations.append(
                    Observation(
                        resource=resource,
                        metric=Metric(
                            name="node_memory_percent",
                            value=float(node.get("memory_percent", 0.0)),
                            unit="%",
                            description="Node memory utilization",
                            labels={"node": node.get("name", "")},
                        ),
                        severity=self._severity_for_percent(node.get("memory_percent", 0.0)),
                    )
                )

        # Deployments
        for dep in deployments:
            dep_id = f"deployment:{dep.get('namespace','default')}:{dep.get('name','unknown')}"
            resource = Resource(
                resource_id=dep_id,
                name=dep.get("name", dep_id),
                type=ResourceType.SERVICE,
                labels={"cluster": self.cluster_name, "namespace": dep.get("namespace", "")},
                metadata={"kubernetes": True},
            )
            resources.append(resource)

            replicas = float(dep.get("replicas", 0))
            available = float(dep.get("available_replicas", replicas))
            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name="deployment_replicas",
                        value=replicas,
                        unit="count",
                        description="Deployment desired replicas",
                        labels={"deployment": dep.get("name", "")},
                    ),
                    severity=Severity.INFO,
                )
            )

            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name="deployment_available_replicas",
                        value=available,
                        unit="count",
                        description="Deployment available replicas",
                        labels={"deployment": dep.get("name", "")},
                    ),
                    severity=Severity.WARNING if available < replicas else Severity.INFO,
                )
            )

        # Pods and Restart counts
        for pod in pods:
            pod_id = f"pod:{pod.get('namespace','default')}:{pod.get('name','unknown')}"
            resource = Resource(
                resource_id=pod_id,
                name=pod.get("name", pod_id),
                type=ResourceType.WORKER,
                labels={
                    "cluster": self.cluster_name,
                    "namespace": pod.get("namespace", ""),
                    "pod": pod.get("name", ""),
                },
                metadata={"kubernetes": True},
            )
            resources.append(resource)

            restarts = float(pod.get("restarts", 0))
            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name="pod_restart_count",
                        value=restarts,
                        unit="count",
                        description="Pod container restart count",
                        labels={"pod": pod.get("name", ""), "namespace": pod.get("namespace", "")},
                    ),
                    severity=Severity.WARNING if restarts > 0 else Severity.INFO,
                )
            )

        # Namespaces
        for ns in namespaces:
            ns_id = f"namespace:{ns}"
            resource = Resource(
                resource_id=ns_id,
                name=ns,
                type=ResourceType.UNKNOWN,
                labels={"cluster": self.cluster_name, "namespace": ns},
                metadata={"kubernetes": True},
            )
            resources.append(resource)

            # count pods in namespace
            pod_count = float(sum(1 for p in pods if p.get("namespace") == ns))
            observations.append(
                Observation(
                    resource=resource,
                    metric=Metric(
                        name="namespace_pod_count",
                        value=pod_count,
                        unit="count",
                        description="Number of pods in namespace",
                        labels={"namespace": ns},
                    ),
                    severity=Severity.INFO,
                )
            )

        # Events
        for evt in events:
            # create a lightweight resource representing the namespace/event
            evt_ns = evt.get("namespace", "default")
            res = Resource(
                resource_id=f"event:{evt.get('id', 'unknown')}",
                name=f"event:{evt.get('id','unknown')}",
                type=ResourceType.UNKNOWN,
                labels={"cluster": self.cluster_name, "namespace": evt_ns},
                metadata={"kubernetes_event": True},
            )
            resources.append(res)

            reason = str(evt.get("reason", ""))
            severity = self._severity_for_event_reason(reason)
            observations.append(
                Observation(
                    resource=res,
                    metric=Metric(
                        name="kubernetes_event",
                        value=1.0,
                        unit="count",
                        description=f"Kubernetes event: {reason}",
                        labels={"reason": reason, "kind": str(evt.get("kind", ""))},
                    ),
                    severity=severity,
                    message=str(evt.get("message", "")),
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
        raise ValueError("expected a list for k8s collection field")

    def _severity_for_percent(self, value: Any) -> Severity:
        try:
            v = float(value)
        except Exception:
            return Severity.INFO
        if v >= 90:
            return Severity.CRITICAL
        if v >= 75:
            return Severity.WARNING
        return Severity.INFO

    def _severity_for_event_reason(self, reason: str) -> Severity:
        if not reason:
            return Severity.INFO
        low = reason.lower()
        if "failed" in low or "error" in low or "crashloop" in low:
            return Severity.ERROR
        if "backoff" in low or "unhealthy" in low or "oom" in low:
            return Severity.WARNING
        return Severity.INFO

    def _default_mock_k8s_data(self) -> dict:
        return {
            "pods": [
                {"name": "web-abc", "namespace": "default", "restarts": 0, "phase": "Running"},
                {"name": "worker-xyz", "namespace": "default", "restarts": 2, "phase": "Running"},
            ],
            "deployments": [
                {"name": "web", "namespace": "default", "replicas": 3, "available_replicas": 3},
                {"name": "worker", "namespace": "default", "replicas": 2, "available_replicas": 1},
            ],
            "nodes": [
                {"name": "node-1", "cpu_percent": 62.5, "memory_percent": 54.3, "ready": True},
                {"name": "node-2", "cpu_percent": 81.0, "memory_percent": 70.1, "ready": True},
            ],
            "events": [
                {"id": "evt-1", "namespace": "default", "kind": "Pod", "reason": "BackOff", "message": "Back-off restarting failed container"},
                {"id": "evt-2", "namespace": "kube-system", "kind": "Node", "reason": "Ready", "message": "Node is ready"},
            ],
            "namespaces": ["default", "kube-system"],
        }
