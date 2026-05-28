from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.auth import CurrentUser as AuthenticatedUser
from core.base.models import BaseModel
from core.context import (
    RequestContext,
    get_current_context,
    reset_current_context,
    set_current_context,
)
from core.db import unit_of_work
from core.exceptions import AppError
from core.tenancy import DatabaseTenantContextResolver, Tenant, TenantMember


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
async def test_database_tenant_context_resolver_uses_auth_user_and_db_facts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _add_tenant(uow.session, tenant_id="tenant-a")
        uow.session.add(TenantMember(tenant_id="tenant-a", user_id="user-1", status="active"))

    token = set_current_context(RequestContext(request_id="req_test"))
    try:
        async with session_factory() as session:
            tenant_id = await DatabaseTenantContextResolver(session).resolve(
                current_user=_auth_user("user-1", tenant_id="tenant-a"),
                operation="read",
            )

        context = get_current_context()
        assert tenant_id == "tenant-a"
        assert context is not None
        assert context.user_id == "user-1"
        assert context.tenant_id == "tenant-a"
        assert context.frozen is True
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_database_tenant_context_resolver_rejects_inactive_membership(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _add_tenant(uow.session, tenant_id="tenant-a")
        uow.session.add(TenantMember(tenant_id="tenant-a", user_id="user-1", status="inactive"))

    async with session_factory() as session:
        with pytest.raises(AppError) as rejected:
            await DatabaseTenantContextResolver(session).resolve(
                current_user=_auth_user("user-1", tenant_id="tenant-a"),
                operation="read",
            )

    assert rejected.value.code == "TENANT_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_database_tenant_context_resolver_rejects_disallowed_tenant_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _add_tenant(uow.session, tenant_id="tenant-a", status="deleting")
        uow.session.add(TenantMember(tenant_id="tenant-a", user_id="user-1", status="active"))

    async with session_factory() as session:
        with pytest.raises(AppError) as rejected:
            await DatabaseTenantContextResolver(session).resolve(
                current_user=_auth_user("user-1", tenant_id="tenant-a"),
                operation="read",
            )

    assert rejected.value.code == "TENANT_STATE_FORBIDDEN"


def _auth_user(user_id: str, *, tenant_id: str | None) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        external_id=None,
        email="owner@example.com",
        display_name="Owner",
        auth_provider="local",
        session_id="sess-1",
        token_version=1,
        tenant_id=tenant_id,
    )


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
