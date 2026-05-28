from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings


def test_health_endpoints_use_envelope() -> None:
    app = create_app(Settings())
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "OK"
    assert body["data"] == {"status": "alive"}
    assert body["request_id"].startswith("req_")
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_ready_endpoint_exposes_runtime_readiness_checks() -> None:
    app = create_app(Settings(installed_apps=["apps.example_domain.module"]))
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "OK"
    assert body["data"]["status"] == "ready"
    assert body["data"]["checks"] == {
        "config_loaded": True,
        "database_configured": True,
        "app_registry_loaded": True,
        "metrics_registry_loaded": True,
    }
    assert body["data"]["details"]["installed_apps"] == ["example_domain"]


def test_cloud_profile_rejects_always_200_mode() -> None:
    settings = Settings(
        app={"env": "cloud"},
        api={"error_http_status_mode": "always_200"},
        security={"jwt_secret": "not-default"},
    )

    try:
        create_app(settings)
    except ValueError as exc:
        assert "standard HTTP status" in str(exc)
    else:
        raise AssertionError("cloud profile accepted always_200 mode")
