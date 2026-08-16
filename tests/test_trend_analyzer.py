from datetime import datetime, timedelta, timezone

import pytest

from monitoring_agent.domain.entities import MetricTrend
from monitoring_agent.domain.services import TrendAnalyzer


def _snapshot(timestamp: datetime, metrics: dict[str, float]) -> dict:
    """Helper to build a normalized snapshot."""
    return {
        "resource_count": 1,
        "metric_count": len(metrics),
        "timestamp": timestamp.isoformat(),
        "resources": [{"resource_id": "svc:test", "name": "test", "type": "service", "labels": {}}],
        "metrics": [
            {
                "name": name,
                "value": value,
                "unit": "count",
                "timestamp": timestamp.isoformat(),
                "labels": {},
                "resource_id": "svc:test",
                "resource_name": "test",
                "collector": "test-collector",
            }
            for name, value in metrics.items()
        ],
        "missing_metric_values": 0,
    }


def test_trend_analyzer_detects_increasing_trend() -> None:
    now = datetime.now(timezone.utc)
    baseline = _snapshot(now - timedelta(minutes=5), {"requests": 100.0})
    current = _snapshot(now, {"requests": 150.0})

    trends = TrendAnalyzer.analyze(current, [baseline], metric_names=["requests"], lookback_windows=1)

    assert len(trends) >= 1
    trend = next((t for t in trends if t.metric_name == "requests"), None)
    assert trend is not None
    assert trend.direction == "up"
    assert trend.current_value == 150.0
    assert trend.baseline == 100.0
    assert trend.slope is not None and trend.slope > 0
    assert trend.confidence >= 0.5


def test_trend_analyzer_detects_decreasing_trend() -> None:
    now = datetime.now(timezone.utc)
    baseline = _snapshot(now - timedelta(minutes=5), {"error_rate": 10.0})
    current = _snapshot(now, {"error_rate": 5.0})

    trends = TrendAnalyzer.analyze(current, [baseline], metric_names=["error_rate"], lookback_windows=1)

    trend = next((t for t in trends if t.metric_name == "error_rate"), None)
    assert trend is not None
    assert trend.direction == "down"
    assert trend.current_value == 5.0
    assert trend.baseline == 10.0
    assert trend.slope is not None and trend.slope < 0


def test_trend_analyzer_detects_stable_trend() -> None:
    now = datetime.now(timezone.utc)
    baseline = _snapshot(now - timedelta(minutes=5), {"latency_p95": 100.0})
    current = _snapshot(now, {"latency_p95": 101.0})

    trends = TrendAnalyzer.analyze(current, [baseline], metric_names=["latency_p95"], lookback_windows=1)

    trend = next((t for t in trends if t.metric_name == "latency_p95"), None)
    assert trend is not None
    assert trend.direction == "stable"
    assert trend.current_value == 101.0
    assert trend.baseline == 100.0


def test_trend_analyzer_detects_sudden_spike() -> None:
    now = datetime.now(timezone.utc)
    baseline = _snapshot(now - timedelta(minutes=5), {"requests": 100.0})
    current = _snapshot(now, {"requests": 300.0})

    trends = TrendAnalyzer.analyze(current, [baseline], metric_names=["requests"], lookback_windows=1, spike_threshold_pct=100.0)

    trend = next((t for t in trends if t.metric_name == "requests"), None)
    assert trend is not None
    assert trend.direction == "up"
    assert trend.slope is not None
    assert abs(trend.slope) >= 100.0


def test_trend_analyzer_detects_sudden_drop() -> None:
    now = datetime.now(timezone.utc)
    baseline = _snapshot(now - timedelta(minutes=5), {"requests": 300.0})
    current = _snapshot(now, {"requests": 100.0})

    trends = TrendAnalyzer.analyze(current, [baseline], metric_names=["requests"], lookback_windows=1, drop_threshold_pct=50.0)

    trend = next((t for t in trends if t.metric_name == "requests"), None)
    assert trend is not None
    assert trend.direction == "down"
    assert trend.slope is not None
    assert abs(trend.slope) >= 50.0


def test_trend_analyzer_respects_lookback_window() -> None:
    now = datetime.now(timezone.utc)
    old = _snapshot(now - timedelta(minutes=10), {"requests": 100.0})
    mid = _snapshot(now - timedelta(minutes=5), {"requests": 150.0})
    current = _snapshot(now, {"requests": 200.0})

    trends = TrendAnalyzer.analyze(current, [old, mid], metric_names=["requests"], lookback_windows=1)

    trend = next((t for t in trends if t.metric_name == "requests"), None)
    assert trend is not None
    assert trend.baseline == 150.0
    assert trend.current_value == 200.0


def test_trend_analyzer_handles_missing_historical_data() -> None:
    now = datetime.now(timezone.utc)
    current = _snapshot(now, {"requests": 100.0})

    trends = TrendAnalyzer.analyze(current, [], metric_names=["requests"])

    assert len(trends) >= 1
    trend = next((t for t in trends if t.metric_name == "requests"), None)
    assert trend is not None
    assert trend.baseline is None
    assert trend.direction == "stable"
    assert trend.confidence < 0.5


def test_trend_analyzer_ignores_metrics_not_in_current_snapshot() -> None:
    now = datetime.now(timezone.utc)
    baseline = _snapshot(now - timedelta(minutes=5), {"requests": 100.0, "errors": 10.0})
    current = _snapshot(now, {"requests": 150.0})

    trends = TrendAnalyzer.analyze(current, [baseline], metric_names=["requests", "errors"])

    request_trend = next((t for t in trends if t.metric_name == "requests"), None)
    assert request_trend is not None
    error_trend = next((t for t in trends if t.metric_name == "errors"), None)
    assert error_trend is None


def test_trend_analyzer_produces_metric_trend_models() -> None:
    now = datetime.now(timezone.utc)
    baseline = _snapshot(now - timedelta(minutes=5), {"requests": 100.0})
    current = _snapshot(now, {"requests": 150.0})

    trends = TrendAnalyzer.analyze(current, [baseline], metric_names=["requests"])

    assert len(trends) >= 1
    for trend in trends:
        assert isinstance(trend, MetricTrend)
        assert trend.metric_name
        assert trend.direction in {"up", "down", "stable"}
        assert 0.0 <= trend.confidence <= 1.0


def test_trend_analyzer_is_deterministic() -> None:
    now = datetime.now(timezone.utc)
    baseline = _snapshot(now - timedelta(minutes=5), {"requests": 100.0})
    current = _snapshot(now, {"requests": 150.0})

    trends_1 = TrendAnalyzer.analyze(current, [baseline], metric_names=["requests"])
    trends_2 = TrendAnalyzer.analyze(current, [baseline], metric_names=["requests"])

    assert len(trends_1) == len(trends_2)
    for t1, t2 in zip(trends_1, trends_2):
        assert t1.metric_name == t2.metric_name
        assert t1.direction == t2.direction
        assert t1.current_value == t2.current_value
        assert t1.baseline == t2.baseline
        assert t1.slope == t2.slope
        assert t1.confidence == t2.confidence
