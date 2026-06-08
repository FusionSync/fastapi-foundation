import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.app import create_app
from core.auth import LocalJwtConfig, LocalJwtProvider, TokenClaims
from core.base.models import BaseModel
from core.config import Settings
from core.outbox import OutboxEvent
from core.permissions import PLATFORM_TENANT_ID, ProjectedPolicy
from core.tenancy import TenantInvitation
from platform_apps.accounts.models import User, UserSession


def test_platform_tenants_api_provisions_invites_accepts_and_lists_members(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-tenants-api.db'}"
    asyncio.run(_seed_platform_actor(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.tenants.module",
                ],
            )
        )
    )

    provision_response = client.post(
        "/api/v1/platform/tenants",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={
            "id": "tenant-a",
            "name": "Tenant A",
            "code": "tenant-a",
            "owner_user_id": "owner-1",
            "deployment_mode": "local",
        },
    )

    assert provision_response.status_code == 200
    assert provision_response.json()["data"] == {
        "id": "tenant-a",
        "name": "Tenant A",
        "code": "tenant-a",
        "status": "active",
        "deployment_mode": "local",
    }

    asyncio.run(_seed_tenant_user_access(database_url))
    create_member_response = client.post(
        "/api/v1/tenants/tenant-a/members",
        headers={
            "Authorization": f"Bearer {_tenant_token()}",
            "X-Tenant-ID": "tenant-a",
        },
        json={"user_id": "user-3"},
    )

    assert create_member_response.status_code == 200
    managed_member = create_member_response.json()["data"]
    assert managed_member["tenant_id"] == "tenant-a"
    assert managed_member["user_id"] == "user-3"
    assert managed_member["status"] == "active"

    update_member_response = client.patch(
        f"/api/v1/tenants/tenant-a/members/{managed_member['id']}",
        headers={
            "Authorization": f"Bearer {_tenant_token()}",
            "X-Tenant-ID": "tenant-a",
        },
        json={"status": "inactive"},
    )

    assert update_member_response.status_code == 200
    assert update_member_response.json()["data"]["status"] == "inactive"

    revoke_issue_response = client.post(
        "/api/v1/tenants/tenant-a/invitations",
        headers={
            "Authorization": f"Bearer {_tenant_token()}",
            "X-Tenant-ID": "tenant-a",
        },
        json={
            "email": "revoke@example.com",
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
    )

    assert revoke_issue_response.status_code == 200
    revoke_response = client.patch(
        "/api/v1/tenants/tenant-a/invitations/"
        f"{revoke_issue_response.json()['data']['id']}/revoke",
        headers={
            "Authorization": f"Bearer {_tenant_token()}",
            "X-Tenant-ID": "tenant-a",
        },
    )

    assert revoke_response.status_code == 200
    assert revoke_response.json()["data"]["status"] == "revoked"

    invite_response = client.post(
        "/api/v1/tenants/tenant-a/invitations",
        headers={
            "Authorization": f"Bearer {_tenant_token()}",
            "X-Tenant-ID": "tenant-a",
        },
        json={
            "email": "New.User@Example.com",
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
    )

    assert invite_response.status_code == 200
    issued = invite_response.json()["data"]
    assert issued["tenant_id"] == "tenant-a"
    assert issued["email"] == "new.user@example.com"
    assert issued["status"] == "pending"
    assert issued["token"]

    asyncio.run(_seed_invited_user(database_url))
    accept_response = client.post(
        "/api/v1/tenant-invitations/accept",
        headers={"Authorization": f"Bearer {_invited_user_token()}"},
        json={"token": issued["token"], "email": "new.user@example.com"},
    )

    assert accept_response.status_code == 200
    assert accept_response.json()["data"]["status"] == "accepted"
    assert accept_response.json()["data"]["accepted_by_user_id"] == "user-2"

    members_response = client.get(
        "/api/v1/tenants/tenant-a/members",
        headers={
            "Authorization": f"Bearer {_tenant_token()}",
            "X-Tenant-ID": "tenant-a",
        },
    )

    assert members_response.status_code == 200
    assert members_response.json()["pagination"]["total"] == 3
    assert {
        (member["tenant_id"], member["user_id"], member["status"])
        for member in members_response.json()["list"]
    } == {
        ("tenant-a", "owner-1", "active"),
        ("tenant-a", "user-2", "active"),
        ("tenant-a", "user-3", "inactive"),
    }

    invitations = asyncio.run(_all(database_url, TenantInvitation))
    events = asyncio.run(_all(database_url, OutboxEvent))
    assert invitations[0].token_hash != issued["token"]
    assert [event.event_type for event in events] == [
        "tenant.created",
        "tenant.member_activated",
        "tenant.invitation_issued",
        "tenant.invitation_revoked",
        "tenant.invitation_issued",
        "tenant.member_activated",
        "tenant.invitation_accepted",
    ]


async def _seed_platform_actor(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                User(
                    id="owner-1",
                    email="owner@example.com",
                    display_name="Owner",
                    status="active",
                    token_version=1,
                )
            )
            session.add(
                UserSession(
                    id="sess-platform",
                    user_id="owner-1",
                    tenant_id=None,
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            session.add(
                ProjectedPolicy(
                    tenant_id=PLATFORM_TENANT_ID,
                    subject="user:owner-1",
                    resource="tenant",
                    action="manage",
                    effect="allow",
                    role_grant_id="grant-platform",
                    policy_version=1,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_tenant_user_access(database_url: str) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                UserSession(
                    id="sess-tenant",
                    user_id="owner-1",
                    tenant_id="tenant-a",
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            for resource, action in (
                ("tenant_invitation", "invite"),
                ("tenant_invitation", "revoke"),
                ("tenant_member", "read"),
                ("tenant_member", "manage"),
            ):
                session.add(
                    ProjectedPolicy(
                        tenant_id="tenant-a",
                        subject="user:owner-1",
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


async def _seed_invited_user(database_url: str) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                User(
                    id="user-2",
                    email="new.user@example.com",
                    display_name="New User",
                    status="active",
                    token_version=1,
                )
            )
            session.add(
                UserSession(
                    id="sess-user-2",
                    user_id="user-2",
                    tenant_id=None,
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def _platform_token() -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id="owner-1",
            session_id="sess-platform",
            auth_provider="local",
            token_version=1,
            tenant_id=None,
        )
    )


def _tenant_token() -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id="owner-1",
            session_id="sess-tenant",
            auth_provider="local",
            token_version=1,
            tenant_id="tenant-a",
        )
    )


def _invited_user_token() -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id="user-2",
            session_id="sess-user-2",
            auth_provider="local",
            token_version=1,
            tenant_id=None,
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
