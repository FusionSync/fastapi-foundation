import asyncio
import json
import sys
import types
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.admin import AdminModelSpec, AdminPermissionSpec, AdminRouteSpec
from core.apps import AppModule, AppRegistry
from core.base.models import BaseModel
from core.cli.main import main
from core.permissions import (
    PermissionRegistry,
    PermissionSpec,
    ProjectedPolicy,
    RoleGrant,
    RoleTemplate,
)


def test_permission_registry_collects_app_module_permissions() -> None:
    app_registry = AppRegistry(["apps.example_domain.module"]).load()

    permission_registry = PermissionRegistry.from_app_registry(app_registry)

    assert permission_registry.errors == []
    assert [
        (permission.app_label, permission.spec.resource, permission.spec.action)
        for permission in permission_registry.permissions
    ] == [
        ("example_domain", "example", "read"),
        ("example_domain", "example", "write"),
    ]


def test_permission_registry_collects_admin_permissions(
    monkeypatch,
) -> None:
    app_module = types.ModuleType("fake_admin_permission_app")
    app_module.module = AppModule(
        label="ops",
        version="0.1.0",
        permissions=[PermissionSpec(resource="dashboard", action="read")],
        admin_permissions=[
            AdminPermissionSpec(resource="platform_settings", action="read"),
        ],
        admin_models=[
            AdminModelSpec(
                admin_id="ops.audit_logs",
                model_path="platform_apps.ops.models.AuditLog",
                label="Audit Logs",
                permissions=[AdminPermissionSpec(resource="audit_logs", action="read")],
            )
        ],
        admin_routes=[
            AdminRouteSpec(
                route_id="ops.rebuild_index",
                path="/admin/ops/rebuild-index",
                methods=("POST",),
                handler_path="platform_apps.ops.admin.rebuild_index",
                permissions=[AdminPermissionSpec(resource="search_index", action="rebuild")],
            )
        ],
    )
    monkeypatch.setitem(sys.modules, "fake_admin_permission_app", app_module)

    registry = PermissionRegistry.from_app_registry(
        AppRegistry(["fake_admin_permission_app"]).load()
    )

    assert registry.errors == []
    assert [
        (
            permission.app_label,
            permission.spec.resource,
            permission.spec.action,
            permission.spec.scope,
        )
        for permission in registry.permissions
    ] == [
        ("ops", "dashboard", "read", "tenant"),
        ("ops", "admin:platform_settings", "read", "platform"),
        ("ops", "admin:audit_logs", "read", "platform"),
        ("ops", "admin:search_index", "rebuild", "platform"),
    ]


def test_permissions_catalog_cli_outputs_stable_json(capsys) -> None:
    exit_code = main(
        [
            "permissions",
            "catalog",
            "--installed-app",
            "apps.example_domain.module",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["permissions"][0]["app_label"] == "example_domain"


def test_permissions_reconcile_cli_outputs_metadata_mode(capsys) -> None:
    exit_code = main(
        [
            "permissions",
            "reconcile",
            "--installed-app",
            "apps.example_domain.module",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["reconciled"] is True
    assert payload["mode"] == "metadata"


def test_permissions_reconcile_cli_detects_projection_drift(
    tmp_path: Path,
    capsys,
) -> None:
    database_url = _sqlite_url(tmp_path)
    asyncio.run(_seed_role_grant_without_projection(database_url))

    exit_code = main(
        [
            "permissions",
            "reconcile",
            "--database-url",
            database_url,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["mode"] == "projection"
    assert payload["repaired"] is False
    assert payload["missing"][0]["role_grant_id"] == "grant-1"


def test_permissions_reconcile_cli_repairs_projection_drift(
    tmp_path: Path,
    capsys,
) -> None:
    database_url = _sqlite_url(tmp_path)
    asyncio.run(_seed_role_grant_without_projection(database_url))

    exit_code = main(
        [
            "permissions",
            "reconcile",
            "--database-url",
            database_url,
            "--repair",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["mode"] == "projection"
    assert payload["repaired"] is True
    assert asyncio.run(_projected_policy_count(database_url)) == 1


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'permissions-cli.db'}"


async def _seed_role_grant_without_projection(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                RoleTemplate(
                    id="template-viewer",
                    scope="tenant",
                    name="viewer",
                    version=1,
                    permissions=[{"resource": "example", "action": "read"}],
                )
            )
            session.add(
                RoleGrant(
                    id="grant-1",
                    tenant_id="tenant-a",
                    subject_type="user",
                    subject_id="user-1",
                    role_template_id="template-viewer",
                    policy_version=1,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _projected_policy_count(database_url: str) -> int:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.scalar(select(func.count()).select_from(ProjectedPolicy))
            return int(result or 0)
    finally:
        await engine.dispose()
