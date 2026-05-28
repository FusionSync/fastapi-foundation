from __future__ import annotations

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
}

_COUNTERS = {
    "http_requests_total",
    "migration_preflight_total",
    "migration_apply_total",
    "tenant_isolation_guard_failures_total",
}
_HISTOGRAMS = {"http_request_duration_seconds", "outbox_dispatch_duration_seconds"}


def render_metrics_contract() -> str:
    lines: list[str] = []
    for name, help_text in sorted(METRIC_NAMES.items()):
        metric_type = _metric_type(name)
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")
        lines.append(f"{name} 0")
    return "\n".join(lines) + "\n"


def _metric_type(name: str) -> str:
    if name in _COUNTERS:
        return "counter"
    if name in _HISTOGRAMS:
        return "histogram"
    return "gauge"
