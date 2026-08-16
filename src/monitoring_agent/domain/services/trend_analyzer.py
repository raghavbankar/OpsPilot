from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from monitoring_agent.domain.entities import MetricTrend


class TrendAnalyzer:
    """Analyze normalized telemetry to detect trends in metrics.

    This analyzer compares current metrics with historical snapshots to:
    - Calculate percentage changes
    - Detect increasing/decreasing trends
    - Detect sudden spikes and drops
    - Produce MetricTrend models

    It is intentionally deterministic and does not perform:
    - health scoring
    - anomaly detection (incident detection)
    - RCA
    - recovery suggestions
    - log/trace collection
    - LLM inference
    """

    @classmethod
    def analyze(
        cls,
        current_snapshot: Mapping[str, Any],
        historical_snapshots: Iterable[Mapping[str, Any]] | None = None,
        *,
        metric_names: Iterable[str] | None = None,
        lookback_windows: int = 1,
        spike_threshold_pct: float = 50.0,
        drop_threshold_pct: float = 50.0,
        stability_threshold_pct: float = 5.0,
    ) -> list[MetricTrend]:
        """Analyze metrics from current snapshot and produce trends.

        Args:
            current_snapshot: Normalized snapshot dict from DataNormalizer.normalize()
            historical_snapshots: List of prior normalized snapshots, most recent first
            metric_names: If provided, only analyze these metrics
            lookback_windows: Number of historical snapshots to use for baseline (max 1 = most recent only)
            spike_threshold_pct: Absolute percentage change to flag as spike
            drop_threshold_pct: Absolute percentage change to flag as drop
            stability_threshold_pct: Threshold below which trend is considered stable

        Returns:
            List of MetricTrend models with direction, slope, confidence, etc.
        """
        if historical_snapshots is None:
            historical_snapshots = []

        historical = list(historical_snapshots)
        requested_metric_names = set(metric_names or [])
        trends: list[MetricTrend] = []

        current_metrics = cls._index_metrics(current_snapshot)
        current_ts = cls._parse_timestamp(current_snapshot.get("timestamp"))

        for metric_name in requested_metric_names:
            current_metric = current_metrics.get(metric_name)
            if current_metric is None:
                continue

            baseline_metric, baseline_ts = cls._get_baseline_metric(historical, metric_name, lookback_windows)

            trend = cls._compute_trend(
                metric_name,
                current_metric,
                current_ts,
                baseline_metric,
                baseline_ts,
                spike_threshold_pct,
                drop_threshold_pct,
                stability_threshold_pct,
            )
            trends.append(trend)

        return trends

    @classmethod
    def _index_metrics(cls, snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        """Build a dict mapping metric_name to metric data."""
        indexed: dict[str, Mapping[str, Any]] = {}
        for metric in snapshot.get("metrics", []) or []:
            if isinstance(metric, Mapping):
                name = metric.get("name")
                if name:
                    indexed[str(name)] = metric
        return indexed

    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        """Parse ISO timestamp string to datetime."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return None
        return None

    @classmethod
    def _get_baseline_metric(
        cls,
        historical_snapshots: list[Mapping[str, Any]],
        metric_name: str,
        lookback_windows: int,
    ) -> tuple[Mapping[str, Any] | None, datetime | None]:
        """Find the most recent historical metric within the lookback window.
        
        Historical snapshots are ordered oldest-to-newest. We look back from the
        most recent (last in list) up to lookback_windows entries.
        """
        if not historical_snapshots:
            return None, None

        lookback = max(1, min(lookback_windows, len(historical_snapshots)))
        for i in range(1, lookback + 1):
            idx = len(historical_snapshots) - i
            if idx < 0:
                break
            snapshot = historical_snapshots[idx]
            if snapshot is None:
                continue
            indexed = cls._index_metrics(snapshot)
            baseline_metric = indexed.get(metric_name)
            if baseline_metric is not None:
                baseline_ts = cls._parse_timestamp(snapshot.get("timestamp"))
                return baseline_metric, baseline_ts
        return None, None

    @classmethod
    def _compute_trend(
        cls,
        metric_name: str,
        current_metric: Mapping[str, Any],
        current_ts: datetime | None,
        baseline_metric: Mapping[str, Any] | None,
        baseline_ts: datetime | None,
        spike_threshold_pct: float,
        drop_threshold_pct: float,
        stability_threshold_pct: float,
    ) -> MetricTrend:
        """Compute trend for a single metric."""
        current_value = cls._extract_numeric(current_metric.get("value"))

        if current_value is None:
            return MetricTrend(metric_name=metric_name, direction="stable", confidence=0.0)

        baseline_value = None
        slope = None
        direction = "stable"
        confidence = 0.0

        if baseline_metric is not None:
            baseline_value = cls._extract_numeric(baseline_metric.get("value"))
            if baseline_value is not None and baseline_value != 0:
                pct_change = cls._percentage_change(baseline_value, current_value)
                slope = pct_change

                if abs(pct_change) <= stability_threshold_pct:
                    direction = "stable"
                    confidence = 0.9
                elif pct_change > 0:
                    direction = "up"
                    confidence = min(1.0, 0.5 + (abs(pct_change) / spike_threshold_pct) * 0.4)
                else:
                    direction = "down"
                    confidence = min(1.0, 0.5 + (abs(pct_change) / drop_threshold_pct) * 0.4)
        else:
            confidence = 0.3

        return MetricTrend(
            metric_name=metric_name,
            direction=direction,
            slope=slope,
            baseline=baseline_value,
            current_value=current_value,
            confidence=confidence,
        )

    @classmethod
    def _extract_numeric(cls, value: Any) -> float | None:
        """Extract numeric value from various types."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, str):
            try:
                return float(value)
            except (ValueError, TypeError):
                return None
        return None

    @classmethod
    def _percentage_change(cls, baseline: float, current: float) -> float:
        """Calculate percentage change from baseline to current."""
        if baseline == 0:
            if current == 0:
                return 0.0
            return 100.0 if current > 0 else -100.0
        return ((current - baseline) / abs(baseline)) * 100.0


__all__ = ["TrendAnalyzer"]
