from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.events import EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxRepository
from core.tenancy import Tenant, TenantLifecycleService
from platform_apps.accounts import AccountsService, User, UserSession


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
        uow.session.add(
            Tenant(
                id="tenant-a",
                name="Tenant A",
                code="tenant-a",
                status="active",
                deployment_mode="local",
            )
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
