import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.app import create_app
from core.auth import LocalJwtConfig, LocalJwtProvider, TokenClaims
from core.base.models import BaseModel
from core.config import Settings
from core.permissions import PLATFORM_TENANT_ID, ProjectedPolicy, RoleGrant, RoleTemplate
from core.tenancy import Tenant, TenantMember
from platform_apps.accounts.models import User, UserSession
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
        "/api/v1/tenants/tenant-a/settings/values/files/max_file_size_mb",
        headers={
            "Authorization": f"Bearer {_tenant_token()}",
            "X-Tenant-ID": "tenant-a",
        },
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
