from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import Lock

METRIC_NAMES = {
    "http_requests_total": "Counter for HTTP requests by route, method, and status class.",
    "http_request_duration_seconds": "Histogram for HTTP request latency.",
    "outbox_events_pending": "Gauge for pending outbox events.",
    "outbox_events_publishing": "Gauge for events claimed by outbox dispatchers.",
    "outbox_events_dead_letter": "Gauge for outbox dead-letter events.",
    "outbox_dispatch_duration_seconds": "Histogram for outbox handler dispatch latency.",
    "migration_preflight_total": "Counter for migration preflight results.",
    "migration_apply_total": "Counter for migration apply results.",
    "tenant_isolation_guard_failures_total": "Counter for tenant isolation guard failures.",
    "rate_limit_hits_total": "Counter for rate limit denials by rule and route class.",
    "quota_exceeded_total": "Counter for quota denials by metric and scope.",
    "external_http_requests_total": "Counter for external HTTP calls by service and result.",
}

_COUNTERS = {
    "http_requests_total",
    "migration_preflight_total",
    "migration_apply_total",
    "tenant_isolation_guard_failures_total",
    "rate_limit_hits_total",
    "quota_exceeded_total",
    "external_http_requests_total",
}
_HISTOGRAMS = {"http_request_duration_seconds", "outbox_dispatch_duration_seconds"}

MetricLabels = Mapping[str, object]
MetricKey = tuple[tuple[str, str], ...]


@dataclass
class MetricsRegistry:
    _values: dict[tuple[str, MetricKey], float] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def increment(
        self,
        name: str,
        labels: MetricLabels | None = None,
        *,
        amount: int | float = 1,
    ) -> None:
        _ensure_metric_exists(name)
        if amount < 0:
            msg = "counter increments must be non-negative"
            raise ValueError(msg)
        key = (name, _normalize_labels(labels))
        with self._lock:
            self._values[key] = self._values.get(key, 0) + float(amount)

    def set_gauge(
        self,
        name: str,
        value: int | float,
        labels: MetricLabels | None = None,
    ) -> None:
        _ensure_metric_exists(name)
        key = (name, _normalize_labels(labels))
        with self._lock:
            self._values[key] = float(value)

    def render(self) -> str:
        with self._lock:
            samples = dict(self._values)

        lines: list[str] = []
        for name, help_text in sorted(METRIC_NAMES.items()):
            metric_type = _metric_type(name)
            base_value = samples.get((name, ()), 0)
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {metric_type}")
            lines.append(f"{name} {_format_number(base_value)}")

        for (name, labels), value in sorted(samples.items(), key=_sort_sample):
            if not labels:
                continue
            lines.append(f"{name}{_format_labels(labels)} {_format_number(value)}")
        return "\n".join(lines) + "\n"


def render_metrics_contract(registry: MetricsRegistry | None = None) -> str:
    registry = registry or MetricsRegistry()
    return registry.render()


def _ensure_metric_exists(name: str) -> None:
    if name not in METRIC_NAMES:
        msg = f"unknown metric: {name}"
        raise ValueError(msg)


def _normalize_labels(labels: MetricLabels | None) -> MetricKey:
    if not labels:
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _sort_sample(item: tuple[tuple[str, MetricKey], float]) -> tuple[str, MetricKey]:
    return item[0]


def _format_labels(labels: MetricKey) -> str:
    if not labels:
        return ""
    pairs = ",".join(f'{key}="{_escape_label_value(value)}"' for key, value in labels)
    return f"{{{pairs}}}"


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_number(value: float) -> str:
    return f"{value:.15g}"


def _metric_type(name: str) -> str:
    if name in _COUNTERS:
        return "counter"
    if name in _HISTOGRAMS:
        return "histogram"
    return "gauge"
