from fastapi.testclient import TestClient

from core.app import create_app
from core.apps.conformance import check_app
from core.config import Settings


def test_example_app_passes_contract_check() -> None:
    result = check_app("apps.example_domain.module")

    assert result.ok is True
    assert result.label == "example_domain"
    assert result.version == "0.1.0"
    assert result.errors == []


def test_example_app_can_be_loaded_from_package_path() -> None:
    result = check_app("apps.example_domain")

    assert result.ok is True
    assert result.label == "example_domain"
    assert result.errors == []


def test_app_factory_registers_example_app_router() -> None:
    app = create_app(Settings(installed_apps=["apps.example_domain.module"]))
    client = TestClient(app)

    response = client.get("/api/v1/examples/ping")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "OK"
    assert body["data"] == {"app": "example_domain", "status": "ready"}
    assert app.state.app_registry.modules[0].label == "example_domain"


def test_example_app_list_route_uses_standard_query_contract() -> None:
    app = create_app(Settings(installed_apps=["apps.example_domain.module"]))
    client = TestClient(app)

    response = client.get(
        "/api/v1/examples",
        params={"page": 2, "page_size": 1, "sort": "-created_at", "title": "demo"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "OK"
    assert body["list"] == [
        {
            "id": "example-2",
            "created_at": "2026-05-28T10:00:00Z",
            "updated_at": "2026-05-28T10:00:00Z",
            "tenant_id": "tenant-demo",
            "title": "demo contract",
        }
    ]
    assert body["pagination"] == {
        "total": 2,
        "page": 2,
        "page_size": 1,
        "has_next": False,
    }
