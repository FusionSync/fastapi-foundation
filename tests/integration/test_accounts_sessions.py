from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.auth import AuthSessionValidator, TokenClaims
from core.base.models import BaseModel
from core.db import unit_of_work
from core.events import EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxRepository
from core.security import PasswordHasher
from core.tenancy import Tenant, TenantLifecycleService, TenantMember
from platform_apps.accounts import (
    AccountsAuthSessionStore,
    AccountsService,
    User,
    UserCredential,
    UserSession,
)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_user_and_session_for_active_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        accounts = AccountsService(uow.session)
        user = await accounts.create_user(
            email="owner@example.com",
            display_name="Owner",
            auth_provider="local",
            external_id="owner@example.com",
        )
        _add_tenant_member(uow.session, tenant_id="tenant-a", user_id=user.id)
        session = await accounts.create_session(
            user_id=user.id,
            tenant_id="tenant-a",
            auth_provider="local",
        )

    assert user.status == "active"
    assert user.token_version == 1
    assert session.status == "active"
    assert session.token_version == 1
    assert session.tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_disabled_user_cannot_create_new_session_and_existing_sessions_are_revoked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        accounts = AccountsService(uow.session)
        user = await accounts.create_user(
            email="owner@example.com",
            display_name="Owner",
            auth_provider="local",
            external_id="owner@example.com",
        )
        _add_tenant_member(uow.session, tenant_id="tenant-a", user_id=user.id)
        active_session = await accounts.create_session(
            user_id=user.id,
            tenant_id="tenant-a",
            auth_provider="local",
        )
        await accounts.disable_user(user.id, reason="security incident")

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as exc_info:
            await AccountsService(uow.session).create_session(
                user_id=user.id,
                tenant_id="tenant-a",
                auth_provider="local",
            )

    sessions = await _sessions(session_factory)
    disabled_user = await _user(session_factory, user.id)
    assert exc_info.value.code == "USER_DISABLED"
    assert disabled_user.status == "disabled"
    assert disabled_user.token_version == 2
    assert [(session.id, session.status, session.revoke_reason) for session in sessions] == [
        (active_session.id, "revoked", "security incident")
    ]


@pytest.mark.asyncio
async def test_tenant_lifecycle_can_revoke_tenant_sessions_through_hook(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()
    registry.register("tenant.suspended", 1, lambda event: None)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        accounts = AccountsService(uow.session)
        user = await accounts.create_user(
            email="owner@example.com",
            display_name="Owner",
            auth_provider="local",
            external_id="owner@example.com",
        )
        _add_tenant_member(uow.session, tenant_id="tenant-a", user_id=user.id)
        _add_tenant_member(uow.session, tenant_id="tenant-b", user_id=user.id)
        await accounts.create_session(
            user_id=user.id,
            tenant_id="tenant-a",
            auth_provider="local",
        )
        await accounts.create_session(
            user_id=user.id,
            tenant_id="tenant-b",
            auth_provider="local",
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        tenant = await uow.session.get(Tenant, "tenant-a")
        assert tenant is not None
        await TenantLifecycleService(
            uow.session,
            OutboxRepository(uow.session, registry=registry),
            session_revocation_hook=AccountsService(uow.session).revoke_tenant_sessions,
        ).suspend_tenant(
            tenant,
            actor_id="owner-1",
            request_id="req-1",
            reason="billing hold",
        )

    sessions = await _sessions(session_factory)
    assert [(session.tenant_id, session.status, session.revoke_reason) for session in sessions] == [
        ("tenant-a", "revoked", "billing hold"),
        ("tenant-b", "active", None),
    ]


@pytest.mark.asyncio
async def test_local_password_flow_and_auth_session_validator_share_session_facts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    hasher = PasswordHasher(iterations=1000, salt="fixed-salt")
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        accounts = AccountsService(uow.session, password_hasher=hasher)
        user = await accounts.create_local_user(
            email="Owner@Example.com",
            display_name="Owner",
            password="CorrectHorse1",
        )
        _add_tenant_member(uow.session, tenant_id="tenant-a", user_id=user.id)
        verified_user = await accounts.verify_local_password(
            email="owner@example.com",
            password="CorrectHorse1",
        )
        user_session = await accounts.create_session(
            user_id=verified_user.id,
            tenant_id="tenant-a",
            auth_provider="local",
        )
        current_user = await AuthSessionValidator(
            AccountsAuthSessionStore(uow.session)
        ).authenticate(
            TokenClaims(
                user_id=user.id,
                session_id=user_session.id,
                auth_provider="local",
                token_version=user.token_version,
                tenant_id="tenant-a",
            )
        )

    credentials = await _credentials(session_factory)
    assert user.email == "owner@example.com"
    assert verified_user.id == user.id
    assert current_user.id == user.id
    assert current_user.email == "owner@example.com"
    assert current_user.session_id == user_session.id
    assert current_user.tenant_id == "tenant-a"
    assert len(credentials) == 1
    assert credentials[0].password_hash != "CorrectHorse1"
    assert hasher.verify_password("CorrectHorse1", credentials[0].password_hash) is True


@pytest.mark.asyncio
async def test_auth_session_validator_rejects_revoked_session_and_disabled_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        accounts = AccountsService(uow.session)
        user = await accounts.create_user(
            email="owner@example.com",
            display_name="Owner",
            auth_provider="local",
            external_id="owner@example.com",
        )
        _add_tenant_member(uow.session, tenant_id="tenant-a", user_id=user.id)
        user_session = await accounts.create_session(
            user_id=user.id,
            tenant_id="tenant-a",
            auth_provider="local",
        )
        await accounts.disable_user(user.id, reason="security incident")
        with pytest.raises(AppError) as rejected:
            await AuthSessionValidator(AccountsAuthSessionStore(uow.session)).authenticate(
                TokenClaims(
                    user_id=user.id,
                    session_id=user_session.id,
                    auth_provider="local",
                    token_version=1,
                    tenant_id="tenant-a",
                )
            )

    assert rejected.value.code == "AUTH_INVALID_TOKEN"
    assert rejected.value.details == {"reason": "session_not_active"}


@pytest.mark.asyncio
async def test_create_session_requires_active_tenant_membership(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        accounts = AccountsService(uow.session)
        user = await accounts.create_user(
            email="owner@example.com",
            display_name="Owner",
            auth_provider="local",
            external_id="owner@example.com",
        )
        _add_tenant(uow.session, tenant_id="tenant-a")

        with pytest.raises(AppError) as non_member:
            await accounts.create_session(
                user_id=user.id,
                tenant_id="tenant-a",
                auth_provider="local",
            )

        uow.session.add(
            TenantMember(tenant_id="tenant-a", user_id=user.id, status="inactive")
        )
        with pytest.raises(AppError) as inactive_member:
            await accounts.create_session(
                user_id=user.id,
                tenant_id="tenant-a",
                auth_provider="local",
            )

    assert non_member.value.code == "TENANT_ACCESS_DENIED"
    assert inactive_member.value.code == "TENANT_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_create_session_requires_tenant_login_allowed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        accounts = AccountsService(uow.session)
        user = await accounts.create_user(
            email="owner@example.com",
            display_name="Owner",
            auth_provider="local",
            external_id="owner@example.com",
        )
        _add_tenant_member(
            uow.session,
            tenant_id="tenant-deleting",
            user_id=user.id,
            status="active",
            tenant_status="deleting",
        )

        with pytest.raises(AppError) as rejected:
            await accounts.create_session(
                user_id=user.id,
                tenant_id="tenant-deleting",
                auth_provider="local",
            )

    assert rejected.value.code == "TENANT_STATE_FORBIDDEN"


async def _user(session_factory: async_sessionmaker[AsyncSession], user_id: str) -> User:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        session.expunge(user)
        return user


async def _sessions(session_factory: async_sessionmaker[AsyncSession]) -> list[UserSession]:
    async with session_factory() as session:
        result = await session.execute(select(UserSession).order_by(UserSession.tenant_id))
        sessions = list(result.scalars().all())
        for user_session in sessions:
            session.expunge(user_session)
        return sessions


async def _credentials(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[UserCredential]:
    async with session_factory() as session:
        result = await session.execute(select(UserCredential))
        credentials = list(result.scalars().all())
        for credential in credentials:
            session.expunge(credential)
        return credentials


def _add_tenant_member(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    status: str = "active",
    tenant_status: str = "active",
) -> None:
    _add_tenant(session, tenant_id=tenant_id, status=tenant_status)
    session.add(TenantMember(tenant_id=tenant_id, user_id=user_id, status=status))


def _add_tenant(
    session: AsyncSession,
    *,
    tenant_id: str,
    status: str = "active",
) -> None:
    session.add(
        Tenant(
            id=tenant_id,
            name=tenant_id,
            code=tenant_id,
            status=status,
            deployment_mode="local",
        )
    )
