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
    assert "http_request_duration_seconds" in response.text
