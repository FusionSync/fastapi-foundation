import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings
from core.operations import DependencyProbeResult


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
    app = create_app(
        Settings(
            database={"url": "sqlite+aiosqlite:///:memory:"},
            installed_apps=["apps.example_domain.module"],
        )
    )
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "OK"
    assert body["data"]["status"] == "ready"
    assert body["data"]["checks"] == {
        "config_loaded": True,
        "database_configured": True,
        "database_reachable": True,
        "app_registry_loaded": True,
        "metrics_registry_loaded": True,
    }
    assert body["data"]["details"]["installed_apps"] == ["example_domain"]
    assert body["data"]["details"]["dependencies"]["database"]["ok"] is True


def test_ready_endpoint_returns_503_when_dependency_probe_fails() -> None:
    app = create_app(Settings(database={"url": "sqlite+aiosqlite:///:memory:"}))
    app.state.readiness_database_probe = _FailingReadinessProbe()
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "OK"
    assert body["data"]["status"] == "not_ready"
    assert body["data"]["checks"]["database_reachable"] is False
    assert body["data"]["details"]["dependencies"]["database"]["error"] == "database down"


def test_missing_route_uses_error_envelope() -> None:
    app = create_app(Settings())
    client = TestClient(app)

    response = client.get("/missing")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["data"] is None
    assert body["list"] is None
    assert body["pagination"] is None
    assert body["details"] == {"path": "/missing"}
    assert body["request_id"].startswith("req_")
    assert response.headers["X-App-Code"] == "NOT_FOUND"
    assert response.headers["X-Request-ID"] == body["request_id"]


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


def test_create_app_rejects_route_returning_raw_dict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_runtime_app(tmp_path, "raw_response", raw_response=True)

    with pytest.raises(ValueError, match="route handler must return core response envelope"):
        create_app(Settings(installed_apps=["runtime_apps.raw_response.module"]))


def test_create_app_rejects_route_without_typed_envelope_response_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_runtime_app(tmp_path, "untyped_response", missing_response_model=True)

    with pytest.raises(ValueError, match="route must declare response_model=Envelope"):
        create_app(Settings(installed_apps=["runtime_apps.untyped_response.module"]))


def test_create_app_rejects_tenant_scoped_model_constraint_violation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_runtime_app(tmp_path, "bad_tenant_model", bad_tenant_model=True)

    with pytest.raises(ValueError, match="tenant scoped constraint violation"):
        create_app(Settings(installed_apps=["runtime_apps.bad_tenant_model.module"]))


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


def test_create_app_exposes_database_runtime() -> None:
    app = create_app(Settings(database={"url": "sqlite+aiosqlite:///:memory:"}))

    assert app.state.database_engine is not None
    assert app.state.session_factory is not None


def test_create_app_auto_wires_declared_auth_session_store() -> None:
    app = create_app(
        Settings(
            database={"url": "sqlite+aiosqlite:///:memory:"},
            installed_apps=["platform_apps.accounts.module"],
            security={"jwt_secret": "test-secret"},
        )
    )

    assert app.state.request_security_pipeline is not None
    assert app.state.request_security_resolver.__self__ is app.state.request_security_pipeline
    assert app.state.route_authorizer.__self__ is app.state.request_security_pipeline


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
    raw_response: bool = False,
    missing_response_model: bool = False,
    bad_tenant_model: bool = False,
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
    if bad_tenant_model:
        _write(
            app_dir / "models.py",
            "from sqlalchemy import String\n"
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from core.base.models import IdMixin, TenantScopedModel\n\n"
            "class BadTenantRecord(IdMixin, TenantScopedModel):\n"
            "    __tablename__ = 'bad_tenant_model_records'\n\n"
            "    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)\n",
        )
    else:
        _write(app_dir / "models.py", "MODEL_IMPORTED = True\n")
    _write(app_dir / "services.py", "class RuntimeService:\n    pass\n")
    if raw_response:
        _write(
            app_dir / "router.py",
            "from core.base import create_router\n\n"
            "router = create_router('/runtime')\n\n"
            "@router.get('/ping')\n"
            "async def ping():\n"
            "    return {'status': 'ok'}\n",
        )
    elif use_raw_router:
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
        decorator = "@router.get('/ping')"
        if not missing_response_model:
            decorator = "@router.get('/ping', response_model=Envelope[RuntimeSchema])"
        _write(
            app_dir / "router.py",
            f"from runtime_apps.{name}.schemas import RuntimeSchema\n"
            "from core.base import create_router\n"
            "from core.serialization import Envelope, ok\n\n"
            "router = create_router('/runtime')\n\n"
            f"{decorator}\n"
            "async def ping():\n"
            "    return ok({'name': 'ok'})\n",
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


class _FailingReadinessProbe:
    async def check(self) -> DependencyProbeResult:
        return DependencyProbeResult(
            ok=False,
            details={"service": "database"},
            error="database down",
        )
