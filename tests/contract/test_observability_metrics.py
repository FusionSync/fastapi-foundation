import logging

import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from core.cache import MemoryCacheProvider
from core.config import Settings
from core.observability import (
    METRIC_NAMES,
    monitoring_contract,
    render_metrics_contract,
)
from core.rate_limit import CacheRateLimiter, RateLimitRegistry, RateLimitRule


def test_metrics_contract_contains_required_cross_cutting_names() -> None:
    metrics = render_metrics_contract()

    for metric_name in METRIC_NAMES:
        assert f"# HELP {metric_name}" in metrics
        assert f"{metric_name} 0" in metrics
    assert "http_requests_total" in metrics
    assert "outbox_events_dead_letter" in metrics
    assert "migration_preflight_total" in metrics
    assert "tenant_isolation_guard_failures_total" in metrics


def test_private_cloud_monitoring_contract_contains_dashboard_and_alerts() -> None:
    private = monitoring_contract("private").to_dict()
    cloud = monitoring_contract("cloud").to_dict()

    assert private["profile"] == "private"
    assert [panel["id"] for panel in private["dashboard_panels"]] == [
        "http_traffic",
        "process_health",
        "outbox_delivery",
        "release_safety",
    ]
    assert any(
        rule["id"] == "config_drift_detected"
        and rule["severity"] == "critical"
        and rule["metric"] == "config_drift_has_drift"
        and "core config drift-check --profile private --json" in rule["runbook"]
        for rule in private["alert_rules"]
    )
    assert any(
        rule["id"] == "process_heartbeat_stale"
        and "service_role" in rule["labels"]
        and "instance_id" in rule["labels"]
        for rule in private["alert_rules"]
    )
    assert any(
        rule["id"] == "release_checkpoint_failed"
        and "--artifact-target docker-compose" in rule["runbook"]
        for rule in private["alert_rules"]
    )
    assert any(
        "release_checkpoint_ok" in panel["metrics"]
        for panel in private["dashboard_panels"]
        if panel["id"] == "release_safety"
    )
    assert any(
        rule["id"] == "cloud_http_5xx_rate_high" and rule["severity"] == "page"
        for rule in cloud["alert_rules"]
    )


def test_metrics_endpoint_exposes_contract() -> None:
    client = TestClient(create_app(Settings()))

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_request_duration_seconds" in response.text


def test_metrics_endpoint_records_http_request_counters() -> None:
    client = TestClient(create_app(Settings()))

    health_response = client.get("/healthz")
    missing_response = client.get("/missing")
    metrics_response = client.get("/metrics")

    assert health_response.status_code == 200
    assert missing_response.status_code == 404
    assert (
        'http_requests_total{method="GET",route="/healthz",status_class="2xx"} 1'
        in metrics_response.text
    )
    assert (
        'http_requests_total{method="GET",route="/missing",status_class="4xx"} 1'
        in metrics_response.text
    )


def test_request_observability_logs_context_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(
        create_app(
            Settings(
                app={"env": "local", "version": "2.3.4"},
                observability={"service_role": "server"},
            )
        )
    )
    caplog.set_level(logging.INFO, logger="core.observability.requests")

    response = client.get(
        "/healthz",
        headers={
            "X-Request-ID": "req-log",
            "X-Trace-ID": "trace-log",
            "User-Agent": "pytest-agent",
        },
    )

    assert response.status_code == 200
    request_logs = [
        record.http_request
        for record in caplog.records
        if record.name == "core.observability.requests"
    ]
    assert request_logs[0].pop("duration_ms") >= 0
    assert request_logs == [
        {
            "request_id": "req-log",
            "trace_id": "trace-log",
            "tenant_id": None,
            "user_id": None,
            "route": "/healthz",
            "method": "GET",
            "status_code": 200,
            "status_class": "2xx",
            "app_code": "OK",
            "deployment_mode": "local",
            "service_role": "server",
            "instance_id": None,
            "version": "2.3.4",
            "ip_address": "testclient",
            "user_agent": "pytest-agent",
        }
    ]


def test_request_observability_logs_rate_limit_denials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(Settings())
    app.state.rate_limit_registry = RateLimitRegistry(
        default_rule=RateLimitRule(
            name="health.probe",
            limit=1,
            window_seconds=30,
            dimensions=("ip_address", "route"),
        )
    )
    app.state.rate_limiter = CacheRateLimiter(MemoryCacheProvider())
    client = TestClient(app)
    caplog.set_level(logging.INFO, logger="core.observability.requests")

    assert client.get("/healthz", headers={"X-Request-ID": "req-first"}).status_code == 200
    caplog.clear()
    response = client.get("/healthz", headers={"X-Request-ID": "req-limited"})

    assert response.status_code == 429
    request_logs = [
        record.http_request
        for record in caplog.records
        if record.name == "core.observability.requests"
    ]
    assert request_logs[0]["request_id"] == "req-limited"
    assert request_logs[0]["status_code"] == 429
    assert request_logs[0]["app_code"] == "RATE_LIMITED"
