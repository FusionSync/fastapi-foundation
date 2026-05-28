from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings
from core.observability import METRIC_NAMES, render_metrics_contract


def test_metrics_contract_contains_required_cross_cutting_names() -> None:
    metrics = render_metrics_contract()

    for metric_name in METRIC_NAMES:
        assert f"# HELP {metric_name}" in metrics
        assert f"{metric_name} 0" in metrics
    assert "http_requests_total" in metrics
    assert "outbox_events_dead_letter" in metrics
    assert "migration_preflight_total" in metrics
    assert "tenant_isolation_guard_failures_total" in metrics


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
