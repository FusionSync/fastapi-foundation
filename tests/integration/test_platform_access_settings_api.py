import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.app import create_app
from core.auth import LocalJwtConfig, LocalJwtProvider, TokenClaims
from core.base.models import BaseModel
from core.cli.main import main
from core.config import Settings
from core.db import unit_of_work
from core.outbox import OutboxDispatcher, OutboxEvent, OutboxEventPublisher, OutboxRepository
from core.permissions import (
    PLATFORM_TENANT_ID,
    AuthorizationDecision,
    ProjectedPolicy,
    RoleGrant,
    RoleTemplate,
)
from core.permissions.services import RoleGrantService
from core.tenancy import Tenant, TenantMember
from platform_apps.accounts.models import User, UserSession
from platform_apps.audit.models import AuditLog
from platform_apps.settings.models import SettingValue


def test_platform_access_api_lists_permissions_and_grants_platform_admin(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-access.db'}"
    asyncio.run(_seed_platform_access_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.access.module",
                ],
            )
        )
    )

    catalog_response = client.get(
        "/api/v1/platform/access/permissions",
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    grant_response = client.post(
        "/api/v1/platform/access/platform-admins",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={
            "user_id": "target-1",
            "role_template_id": "template-platform-admin",
            "reason": "bootstrap admin",
        },
    )

    assert catalog_response.status_code == 200
    assert ("platform_access", "access.platform_admin", "manage") in {
        (item["app_label"], item["resource"], item["action"])
        for item in catalog_response.json()["list"]
    }
    assert grant_response.status_code == 200
    assert grant_response.json()["data"]["tenant_id"] == PLATFORM_TENANT_ID
    assert grant_response.json()["data"]["subject_id"] == "target-1"
    grants = asyncio.run(_all(database_url, RoleGrant))
    assert any(
        grant.tenant_id == PLATFORM_TENANT_ID
        and grant.subject_id == "target-1"
        and grant.role_template_id == "template-platform-admin"
        for grant in grants
    )


def test_platform_access_control_plane_manages_tenant_role_grants(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-access-control.db'}"
    asyncio.run(_seed_platform_access_control_plane_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.access.module",
                    "platform_apps.files.module",
                ],
            )
        )
    )

    template_response = client.post(
        "/api/v1/platform/access/role-templates",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={
            "scope": "tenant",
            "name": "project-reader",
            "permissions": [{"resource": "file", "action": "download"}],
        },
    )

    assert template_response.status_code == 200
    template = template_response.json()["data"]
    assert template["scope"] == "tenant"
    assert template["permissions"] == [{"resource": "file", "action": "download"}]

    list_templates_response = client.get(
        "/api/v1/platform/access/role-templates",
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert list_templates_response.status_code == 200
    assert template["id"] in {item["id"] for item in list_templates_response.json()["list"]}

    grant_response = client.post(
        "/api/v1/access/role-grants",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        json={
            "subject_type": "user",
            "subject_id": "target-1",
            "role_template_id": template["id"],
            "reason": "project reader",
        },
    )

    assert grant_response.status_code == 200
    grant = grant_response.json()["data"]
    assert grant["tenant_id"] == "tenant-a"
    assert grant["subject_id"] == "target-1"

    projected = asyncio.run(_all(database_url, ProjectedPolicy))
    assert any(
        policy.tenant_id == "tenant-a"
        and policy.subject == "user:target-1"
        and policy.resource == "file"
        and policy.action == "download"
        and policy.role_grant_id == grant["id"]
        for policy in projected
    )

    grants_response = client.get(
        "/api/v1/access/role-grants",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
    )
    assert grants_response.status_code == 200
    assert grant["id"] in {item["id"] for item in grants_response.json()["list"]}

    effective_response = client.get(
        "/api/v1/platform/access/subjects/user/target-1/effective-permissions",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"tenant_id": "tenant-a"},
    )
    assert effective_response.status_code == 200
    assert ("file", "download") in {
        (item["resource"], item["action"])
        for item in effective_response.json()["list"]
    }

    revoke_response = client.delete(
        f"/api/v1/access/role-grants/{grant['id']}",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        params={"reason": "cleanup"},
    )
    assert revoke_response.status_code == 200

    projected_after_revoke = asyncio.run(_all(database_url, ProjectedPolicy))
    assert not any(policy.role_grant_id == grant["id"] for policy in projected_after_revoke)


def test_platform_access_role_grants_require_reason_reject_duplicates_and_audit(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-access-audit.db'}"
    asyncio.run(_seed_platform_access_control_plane_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.access.module",
                    "platform_apps.audit.module",
                    "platform_apps.files.module",
                ],
            )
        )
    )

    template_response = client.post(
        "/api/v1/platform/access/role-templates",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={
            "scope": "tenant",
            "name": "project-reader",
            "permissions": [{"resource": "file", "action": "download"}],
        },
    )
    assert template_response.status_code == 200
    template_id = template_response.json()["data"]["id"]

    missing_reason_response = client.post(
        "/api/v1/access/role-grants",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        json={
            "subject_type": "user",
            "subject_id": "target-1",
            "role_template_id": template_id,
        },
    )
    assert missing_reason_response.status_code == 400

    grant_response = client.post(
        "/api/v1/access/role-grants",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        json={
            "subject_type": "user",
            "subject_id": "target-1",
            "role_template_id": template_id,
            "reason": "project access review",
        },
    )
    assert grant_response.status_code == 200
    grant_id = grant_response.json()["data"]["id"]

    duplicate_response = client.post(
        "/api/v1/access/role-grants",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        json={
            "subject_type": "user",
            "subject_id": "target-1",
            "role_template_id": template_id,
            "reason": "duplicate",
        },
    )
    assert duplicate_response.status_code == 409

    missing_revoke_reason_response = client.delete(
        f"/api/v1/access/role-grants/{grant_id}",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
    )
    assert missing_revoke_reason_response.status_code == 400

    revoke_response = client.delete(
        f"/api/v1/access/role-grants/{grant_id}",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        params={"reason": "access no longer needed"},
    )
    assert revoke_response.status_code == 200

    audit_logs = asyncio.run(_all(database_url, AuditLog))
    assert [
        (audit_log.action, audit_log.reason, audit_log.resource_id)
        for audit_log in audit_logs
        if audit_log.resource_type == "role_grant"
    ] == [
        ("role.granted", "project access review", grant_id),
        ("role.revoked", "access no longer needed", grant_id),
    ]


def test_platform_access_current_tenant_routes_and_me_permissions(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-access-current.db'}"
    asyncio.run(_seed_platform_access_control_plane_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.access.module",
                    "platform_apps.files.module",
                ],
            )
        )
    )

    template_response = client.post(
        "/api/v1/platform/access/role-templates",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={
            "scope": "tenant",
            "name": "project-reader",
            "permissions": [{"resource": "file", "action": "download"}],
        },
    )
    assert template_response.status_code == 200
    template_id = template_response.json()["data"]["id"]

    grant_response = client.post(
        "/api/v1/access/role-grants",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        json={
            "subject_type": "user",
            "subject_id": "target-1",
            "role_template_id": template_id,
            "reason": "current tenant route",
        },
    )
    assert grant_response.status_code == 200
    assert grant_response.json()["data"]["tenant_id"] == "tenant-a"

    grants_response = client.get(
        "/api/v1/access/role-grants",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
    )
    assert grants_response.status_code == 200
    assert [item["id"] for item in grants_response.json()["list"]] == [
        grant_response.json()["data"]["id"]
    ]

    me_permissions_response = client.get(
        "/api/v1/me/permissions",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
    )
    assert me_permissions_response.status_code == 200
    assert ("role_grant", "grant") in {
        (item["resource"], item["action"]) for item in me_permissions_response.json()["list"]
    }

    check_response = client.post(
        "/api/v1/me/permissions/check",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        json={"permissions": ["role_grant:grant", "file:download"]},
    )
    assert check_response.status_code == 200
    assert check_response.json()["data"]["permissions"] == [
        {
            "permission": "role_grant:grant",
            "resource": "role_grant",
            "action": "grant",
            "allowed": True,
        },
        {"permission": "file:download", "resource": "file", "action": "download", "allowed": False},
    ]


def test_platform_access_app_registered_role_grant_handler_projects_outbox_events(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-access-handler.db'}"
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=[
                "platform_apps.accounts.module",
                "platform_apps.access.module",
                "platform_apps.files.module",
            ],
        )
    )
    asyncio.run(_seed_role_grant_handler_facts(database_url, app.state.event_registry))

    asyncio.run(_dispatch_outbox_once(app.state.session_factory, app.state.event_registry))

    projected = asyncio.run(_all(database_url, ProjectedPolicy))
    events = asyncio.run(_all(database_url, OutboxEvent))
    assert [(policy.subject, policy.resource, policy.action) for policy in projected] == [
        ("user:target-1", "file", "download")
    ]
    assert [event.status for event in events] == ["published"]


def test_platform_settings_api_resolves_platform_and_tenant_overrides(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-settings.db'}"
    asyncio.run(_seed_platform_settings_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.settings.module",
                ],
            )
        )
    )

    definitions_response = client.get(
        "/api/v1/platform/settings/definitions",
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    platform_value_response = client.put(
        "/api/v1/platform/settings/values/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"value": 128, "reason": "platform default"},
    )
    platform_resolve_response = client.get(
        "/api/v1/platform/settings/resolve/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"tenant_id": "tenant-a"},
    )
    tenant_value_response = client.put(
        "/api/v1/settings/values/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        json={"value": 64, "reason": "tenant override"},
    )
    tenant_resolve_response = client.get(
        "/api/v1/platform/settings/resolve/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"tenant_id": "tenant-a"},
    )

    assert definitions_response.status_code == 200
    assert ("files", "max_file_size_mb") in {
        (item["module"], item["key"]) for item in definitions_response.json()["list"]
    }
    assert platform_value_response.status_code == 200
    assert platform_resolve_response.status_code == 200
    assert platform_resolve_response.json()["data"] == {
        "module": "files",
        "key": "max_file_size_mb",
        "scope": "platform",
        "scope_id": PLATFORM_TENANT_ID,
        "source": "platform",
        "value": 128,
        "version": 1,
    }
    assert tenant_value_response.status_code == 200
    assert tenant_resolve_response.json()["data"] == {
        "module": "files",
        "key": "max_file_size_mb",
        "scope": "tenant",
        "scope_id": "tenant-a",
        "source": "tenant",
        "value": 64,
        "version": 1,
    }
    values = asyncio.run(_all(database_url, SettingValue))
    assert {(value.scope, value.scope_id, value.value_json) for value in values} == {
        ("platform", PLATFORM_TENANT_ID, 128),
        ("tenant", "tenant-a", 64),
    }


def test_platform_settings_current_tenant_value_routes(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-settings-current.db'}"
    asyncio.run(_seed_platform_settings_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.settings.module",
                ],
            )
        )
    )

    upsert_response = client.put(
        "/api/v1/settings/values/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
        json={"value": 64, "reason": "current tenant override"},
    )
    assert upsert_response.status_code == 200
    assert upsert_response.json()["data"]["scope"] == "tenant"
    assert upsert_response.json()["data"]["scope_id"] == "tenant-a"

    list_response = client.get(
        "/api/v1/settings/values",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
    )
    assert list_response.status_code == 200
    assert [(item["key"], item["value"]) for item in list_response.json()["list"]] == [
        ("max_file_size_mb", 64)
    ]


def test_platform_settings_api_lists_history_resets_versions_and_audits(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-settings-control.db'}"
    asyncio.run(_seed_platform_settings_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.audit.module",
                    "platform_apps.settings.module",
                ],
            )
        )
    )

    first_response = client.put(
        "/api/v1/platform/settings/values/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"value": 128, "reason": "initial"},
    )
    assert first_response.status_code == 200
    assert first_response.json()["data"]["version"] == 1

    second_response = client.put(
        "/api/v1/platform/settings/values/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"value": 256, "expected_version": 1, "reason": "increase"},
    )
    assert second_response.status_code == 200
    assert second_response.json()["data"]["version"] == 2

    stale_response = client.put(
        "/api/v1/platform/settings/values/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"value": 512, "expected_version": 1, "reason": "stale"},
    )
    assert stale_response.status_code == 409

    values_response = client.get(
        "/api/v1/platform/settings/values",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"scope": "platform"},
    )
    assert values_response.status_code == 200
    assert ("files", "max_file_size_mb", 256) in {
        (item["module"], item["key"], item["value"])
        for item in values_response.json()["list"]
    }

    value_response = client.get(
        "/api/v1/platform/settings/values/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"scope": "platform"},
    )
    assert value_response.status_code == 200
    assert value_response.json()["data"]["value"] == 256

    history_response = client.get(
        "/api/v1/platform/settings/values/files/max_file_size_mb/history",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"scope": "platform"},
    )
    assert history_response.status_code == 200
    assert [item["version"] for item in history_response.json()["list"]] == [1, 2]

    reset_response = client.delete(
        "/api/v1/platform/settings/values/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"scope": "platform", "reason": "restore default"},
    )
    assert reset_response.status_code == 200
    assert reset_response.json()["data"]["status"] == "reset"

    resolved_response = client.get(
        "/api/v1/platform/settings/resolve/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resolved_response.status_code == 200
    assert resolved_response.json()["data"]["source"] == "default"
    assert resolved_response.json()["data"]["value"] == 50

    audit_logs = asyncio.run(_all(database_url, AuditLog))
    assert sum(
        1
        for audit_log in audit_logs
        if audit_log.action == "platform_settings.value_changed"
        and audit_log.resource_type == "setting_value"
    ) == 3


def test_platform_settings_validate_api_dry_runs_without_writing_values(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-settings-validate.db'}"
    asyncio.run(_seed_platform_settings_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.settings.module",
                ],
            )
        )
    )

    valid_response = client.post(
        "/api/v1/platform/settings/validate/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"scope": "platform", "value": 256},
    )
    assert valid_response.status_code == 200
    assert valid_response.json()["data"] == {
        "module": "files",
        "key": "max_file_size_mb",
        "scope": "platform",
        "scope_id": PLATFORM_TENANT_ID,
        "value": 256,
        "secret_ref": None,
        "value_type": "int",
        "valid": True,
        "dry_run": True,
    }
    assert asyncio.run(_all(database_url, SettingValue)) == []

    invalid_value_response = client.post(
        "/api/v1/platform/settings/validate/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"scope": "platform", "value": 0},
    )
    tenant_scope_response = client.post(
        "/api/v1/platform/settings/validate/files/max_file_size_mb",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"scope": "tenant", "value": 256},
    )
    unsupported_scope_response = client.post(
        "/api/v1/platform/settings/validate/auth/password_min_length",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"scope": "tenant", "scope_id": "tenant-a", "value": 12},
    )
    unknown_response = client.post(
        "/api/v1/platform/settings/validate/files/unknown",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"scope": "platform", "value": 1},
    )

    assert invalid_value_response.status_code == 400
    assert tenant_scope_response.status_code == 400
    assert unsupported_scope_response.status_code == 400
    assert unknown_response.status_code == 400


def test_platform_access_cli_bootstraps_first_platform_admin(
    tmp_path: Path,
    capsys,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-admin-bootstrap.db'}"
    asyncio.run(_seed_platform_admin_bootstrap_facts(database_url))

    exit_code = main(
        [
            "permissions",
            "bootstrap-platform-admin",
            "--database-url",
            database_url,
            "--user-id",
            "admin-1",
            "--installed-app",
            "platform_apps.access.module",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["tenant_id"] == PLATFORM_TENANT_ID
    assert payload["subject_id"] == "admin-1"
    assert payload["role_template_id"] == "platform-admin"
    assert payload["projected_permissions"] > 0

    grants = asyncio.run(_all(database_url, RoleGrant))
    policies = asyncio.run(_all(database_url, ProjectedPolicy))
    assert [(grant.tenant_id, grant.subject_id, grant.role_template_id) for grant in grants] == [
        (PLATFORM_TENANT_ID, "admin-1", "platform-admin")
    ]
    assert ("user:admin-1", "access.platform_admin", "manage") in {
        (policy.subject, policy.resource, policy.action) for policy in policies
    }

    repeated_exit_code = main(
        [
            "permissions",
            "bootstrap-platform-admin",
            "--database-url",
            database_url,
            "--user-id",
            "target-1",
            "--installed-app",
            "platform_apps.access.module",
            "--json",
        ]
    )
    repeated_payload = json.loads(capsys.readouterr().out)
    assert repeated_exit_code == 1
    assert repeated_payload["ok"] is False
    assert repeated_payload["error"]["code"] == "CONFLICT"


async def _seed_platform_access_facts(database_url: str) -> None:
    await _seed_common_facts(database_url)
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                RoleTemplate(
                    id="template-platform-admin",
                    scope="platform",
                    name="platform-admin",
                    version=1,
                    permissions=[
                        {"resource": "access.platform_admin", "action": "manage"}
                    ],
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_platform_access_control_plane_facts(database_url: str) -> None:
    await _seed_common_facts(database_url)
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                UserSession(
                    id="sess-tenant",
                    user_id="admin-1",
                    tenant_id="tenant-a",
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            session.add(TenantMember(tenant_id="tenant-a", user_id="admin-1", status="active"))
            for resource, action, tenant_id in (
                ("access.role_template", "read", PLATFORM_TENANT_ID),
                ("access.role_template", "manage", PLATFORM_TENANT_ID),
                ("access.effective", "read", PLATFORM_TENANT_ID),
                ("access.reconcile", "manage", PLATFORM_TENANT_ID),
                ("role_grant", "read", "tenant-a"),
                ("role_grant", "grant", "tenant-a"),
                ("role_grant", "revoke", "tenant-a"),
            ):
                session.add(
                    ProjectedPolicy(
                        tenant_id=tenant_id,
                        subject="user:admin-1",
                        resource=resource,
                        action=action,
                        effect="allow",
                        role_grant_id=f"grant-{tenant_id}-{resource}-{action}",
                        policy_version=1,
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_platform_settings_facts(database_url: str) -> None:
    await _seed_common_facts(database_url)
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                UserSession(
                    id="sess-tenant",
                    user_id="admin-1",
                    tenant_id="tenant-a",
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            session.add(TenantMember(tenant_id="tenant-a", user_id="admin-1", status="active"))
            for resource, action in (
                ("settings.tenant", "manage"),
                ("settings.tenant", "read"),
            ):
                session.add(
                    ProjectedPolicy(
                        tenant_id="tenant-a",
                        subject="user:admin-1",
                        resource=resource,
                        action=action,
                        effect="allow",
                        role_grant_id=f"grant-{resource}-{action}",
                        policy_version=1,
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_platform_admin_bootstrap_facts(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add_all(
                [
                    User(
                        id="admin-1",
                        email="admin@example.com",
                        display_name="Admin",
                        status="active",
                        token_version=1,
                    ),
                    User(
                        id="target-1",
                        email="target@example.com",
                        display_name="Target",
                        status="active",
                        token_version=1,
                    ),
                ]
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_role_grant_handler_facts(database_url: str, event_registry) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                RoleTemplate(
                    id="template-file-reader",
                    scope="tenant",
                    name="file-reader",
                    version=1,
                    permissions=[{"resource": "file", "action": "download"}],
                )
            )
            await RoleGrantService(
                uow.session,
                OutboxEventPublisher(OutboxRepository(uow.session, registry=event_registry)),
            ).grant_role(
                tenant_id="tenant-a",
                subject_type="user",
                subject_id="target-1",
                role_template_id="template-file-reader",
                actor_id="admin-1",
                request_id="req-handler",
                authorization_decision=AuthorizationDecision(
                    allowed=True,
                    tenant_id="tenant-a",
                    user_id="admin-1",
                    resource="role_grant",
                    action="grant",
                    reason="matched_projected_policy",
                    policy_version=1,
                ),
                reason="handler projection",
            )
    finally:
        await engine.dispose()


async def _seed_common_facts(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                Tenant(
                    id="tenant-a",
                    name="Tenant A",
                    code="tenant-a",
                    status="active",
                    deployment_mode="local",
                )
            )
            session.add_all(
                [
                    User(
                        id="admin-1",
                        email="admin@example.com",
                        display_name="Admin",
                        status="active",
                        token_version=1,
                    ),
                    User(
                        id="target-1",
                        email="target@example.com",
                        display_name="Target",
                        status="active",
                        token_version=1,
                    ),
                    UserSession(
                        id="sess-platform",
                        user_id="admin-1",
                        tenant_id=None,
                        auth_provider="local",
                        status="active",
                        token_version=1,
                    ),
                ]
            )
            for resource, action in (
                ("access.permission", "read"),
                ("access.platform_admin", "manage"),
                ("settings.definition", "read"),
                ("settings.value", "manage"),
                ("settings.value", "read"),
            ):
                session.add(
                    ProjectedPolicy(
                        tenant_id=PLATFORM_TENANT_ID,
                        subject="user:admin-1",
                        resource=resource,
                        action=action,
                        effect="allow",
                        role_grant_id=f"grant-{resource}-{action}",
                        policy_version=1,
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()


async def _dispatch_outbox_once(session_factory, event_registry) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        stats = await OutboxDispatcher(
            OutboxRepository(uow.session, registry=event_registry),
            event_registry,
            dispatcher_id="platform-access-handler-test",
            batch_size=10,
        ).dispatch_once()
        assert stats.published == 1


def _platform_token() -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id="admin-1",
            session_id="sess-platform",
            auth_provider="local",
            token_version=1,
            tenant_id=None,
        )
    )


def _tenant_token() -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id="admin-1",
            session_id="sess-tenant",
            auth_provider="local",
            token_version=1,
            tenant_id="tenant-a",
        )
    )


async def _all(database_url: str, model: type):
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            rows = list((await session.execute(select(model))).scalars().all())
            for row in rows:
                session.expunge(row)
            return rows
    finally:
        await engine.dispose()
