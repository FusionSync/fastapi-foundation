import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.app import create_app
from core.auth import LocalJwtConfig, LocalJwtProvider, TokenClaims
from core.base.models import BaseModel
from core.config import Settings
from core.db import unit_of_work
from core.permissions import PLATFORM_TENANT_ID, ProjectedPolicy
from platform_apps.accounts import AccountsService, ExternalIdentity, UserSession


def test_platform_accounts_api_manages_profile_password_identity_and_sessions(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-accounts-api.db'}"
    seeded = asyncio.run(_seed_accounts_api_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=["platform_apps.accounts.module"],
            )
        )
    )

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "User@Example.com", "password": "OldPass123"},
    )

    assert login_response.status_code == 200
    access_token = login_response.json()["data"]["access_token"]
    user_session_id = login_response.json()["data"]["session"]["id"]

    me_response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert me_response.status_code == 200
    assert me_response.json()["data"]["email"] == "user@example.com"
    assert me_response.json()["data"]["display_name"] == "Old Name"

    profile_response = client.patch(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"display_name": "New Name"},
    )

    assert profile_response.status_code == 200
    assert profile_response.json()["data"]["display_name"] == "New Name"

    password_response = client.patch(
        "/api/v1/me/password",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"current_password": "OldPass123", "new_password": "NewPass456"},
    )

    assert password_response.status_code == 200
    assert password_response.json()["data"]["password_updated"] is True

    bind_response = client.post(
        "/api/v1/me/external-identities",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"provider": "logto", "subject": "logto-user-1"},
    )

    assert bind_response.status_code == 200
    assert bind_response.json()["data"] == {
        "id": bind_response.json()["data"]["id"],
        "provider": "logto",
        "subject": "logto-user-1",
        "user_id": seeded["user_id"],
    }

    sessions_response = client.get(
        "/api/v1/me/sessions",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert sessions_response.status_code == 200
    assert {
        (session["id"], session["status"], session["auth_provider"])
        for session in sessions_response.json()["list"]
    } == {(user_session_id, "active", "local")}

    logout_response = client.delete(
        f"/api/v1/me/sessions/{user_session_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert logout_response.status_code == 200
    assert logout_response.json()["data"] == {"revoked_sessions": 1}

    rejected = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["code"] == "AUTH_INVALID_TOKEN"

    old_password_response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "OldPass123"},
    )
    assert old_password_response.status_code == 401

    new_password_response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "NewPass456"},
    )
    assert new_password_response.status_code == 200

    create_user_response = client.post(
        "/api/v1/platform/accounts/users",
        headers={"Authorization": f"Bearer {_admin_token(seeded)}"},
        json={
            "email": "created@example.com",
            "display_name": "Created User",
            "password": "CreatedPass123",
        },
    )

    assert create_user_response.status_code == 200
    created_user_id = create_user_response.json()["data"]["id"]
    assert create_user_response.json()["data"]["email"] == "created@example.com"

    created_login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "created@example.com", "password": "CreatedPass123"},
    )
    assert created_login_response.status_code == 200

    revoke_response = client.post(
        f"/api/v1/platform/accounts/users/{created_user_id}/sessions/revoke",
        headers={"Authorization": f"Bearer {_admin_token(seeded)}"},
        json={"reason": "admin requested"},
    )

    assert revoke_response.status_code == 200
    assert revoke_response.json()["data"] == {"revoked_sessions": 1}

    disabled_response = client.patch(
        f"/api/v1/platform/accounts/users/{created_user_id}/disable",
        headers={"Authorization": f"Bearer {_admin_token(seeded)}"},
        json={"reason": "security review"},
    )

    assert disabled_response.status_code == 200
    assert disabled_response.json()["data"]["status"] == "disabled"

    identities = asyncio.run(_all(database_url, ExternalIdentity))
    sessions = asyncio.run(_all(database_url, UserSession))
    assert any(identity.provider == "logto" for identity in identities)
    assert any(
        session.user_id == created_user_id
        and session.status == "revoked"
        and session.revoke_reason == "admin requested"
        for session in sessions
    )


async def _seed_accounts_api_facts(database_url: str) -> dict[str, str]:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            accounts = AccountsService(uow.session)
            user = await accounts.create_local_user(
                email="user@example.com",
                display_name="Old Name",
                password="OldPass123",
            )
            admin = await accounts.create_local_user(
                email="admin@example.com",
                display_name="Admin",
                password="AdminPass123",
            )
            admin_session = await accounts.create_session(
                user_id=admin.id,
                tenant_id=None,
                auth_provider="local",
            )
            for resource, action in (
                ("user", "manage"),
                ("session", "revoke"),
            ):
                uow.session.add(
                    ProjectedPolicy(
                        tenant_id=PLATFORM_TENANT_ID,
                        subject=f"user:{admin.id}",
                        resource=resource,
                        action=action,
                        effect="allow",
                        role_grant_id=f"grant-{resource}-{action}",
                        policy_version=1,
                    )
                )
            return {
                "user_id": user.id,
                "admin_id": admin.id,
                "admin_session_id": admin_session.id,
            }
    finally:
        await engine.dispose()


def _admin_token(seeded: dict[str, str]) -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id=seeded["admin_id"],
            session_id=seeded["admin_session_id"],
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
