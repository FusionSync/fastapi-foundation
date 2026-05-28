import sys
from pathlib import Path

import pytest
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


def test_create_app_rejects_non_conforming_installed_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_runtime_app(tmp_path, "bad_runtime", declare_permissions=False)

    with pytest.raises(ValueError, match="App conformance failed"):
        create_app(Settings(installed_apps=["runtime_apps.bad_runtime.module"]))


def test_create_app_rejects_router_without_core_security_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_runtime_app(tmp_path, "raw_router", use_raw_router=True)

    with pytest.raises(ValueError, match="router must be created with core.base.create_router"):
        create_app(Settings(installed_apps=["runtime_apps.raw_router.module"]))


def test_create_app_assembles_runtime_registries_and_imports_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_runtime_app(tmp_path, "runtime_app")
    sys.modules.pop("runtime_apps.runtime_app.models", None)

    app = create_app(Settings(installed_apps=["runtime_apps.runtime_app.module"]))

    assert "runtime_apps.runtime_app.models" in sys.modules
    assert app.state.permission_registry.permissions[0].app_label == "runtime_app"
    assert app.state.migration_registry.errors == []
    assert app.state.event_registry.to_dict() == {"handlers": []}
    assert app.state.task_registry.to_dict() == {"tasks": []}
    assert app.state.schedule_registry.to_dict() == {"schedules": []}
    assert app.state.admin_registry.to_dict()["admin_permissions"] == []


def test_default_app_router_rejects_anonymous_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_runtime_app(tmp_path, "protected_runtime")
    app = create_app(Settings(installed_apps=["runtime_apps.protected_runtime.module"]))
    client = TestClient(app)

    response = client.get("/api/v1/runtime/ping")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_INVALID_TOKEN"


def _write_runtime_app(
    root: Path,
    name: str,
    *,
    declare_permissions: bool = True,
    use_raw_router: bool = False,
) -> None:
    app_dir = root / "runtime_apps" / name
    migrations_dir = app_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (root / "runtime_apps" / "__init__.py").touch()
    _write(app_dir / "__init__.py", f"from runtime_apps.{name}.module import module\n")
    _write(
        app_dir / "schemas.py",
        "from core.base import BaseSchema\n\nclass RuntimeSchema(BaseSchema):\n    name: str\n",
    )
    _write(app_dir / "models.py", "MODEL_IMPORTED = True\n")
    _write(app_dir / "services.py", "class RuntimeService:\n    pass\n")
    if use_raw_router:
        _write(
            app_dir / "router.py",
            "from fastapi import APIRouter\n"
            "from core.serialization import ok\n\n"
            "router = APIRouter(prefix='/runtime')\n\n"
            "@router.get('/ping')\n"
            "async def ping():\n"
            "    return ok({'status': 'ok'})\n",
        )
    else:
        _write(
            app_dir / "router.py",
            "from core.base import create_router\n"
            "from core.serialization import ok\n\n"
            "router = create_router('/runtime')\n\n"
            "@router.get('/ping')\n"
            "async def ping():\n"
            "    return ok({'status': 'ok'})\n",
        )
    _write(
        app_dir / "permissions.py",
        "from core.permissions import PermissionSpec\n\n"
        "PERMISSIONS = [PermissionSpec(resource='runtime', action='read')]\n",
    )
    _write(migrations_dir / "__init__.py", "")
    _write(migrations_dir / "manifest.py", "MIGRATIONS = []\n")
    permissions_expr = "PERMISSIONS" if declare_permissions else "[]"
    _write(
        app_dir / "module.py",
        "from runtime_apps.{name}.permissions import PERMISSIONS\n"
        "from runtime_apps.{name}.router import router\n"
        "from core.apps import AppModule, MigrationSpec\n\n"
        "module = AppModule(\n"
        "    label={name!r},\n"
        "    version='0.1.0',\n"
        "    routers=[router],\n"
        "    models=['runtime_apps.{name}.models'],\n"
        "    migrations=MigrationSpec(path='runtime_apps.{name}.migrations'),\n"
        f"    permissions={permissions_expr},\n"
        ")\n".format(name=name),
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _purge_runtime_apps() -> None:
    for name in list(sys.modules):
        if name == "runtime_apps" or name.startswith("runtime_apps."):
            del sys.modules[name]
