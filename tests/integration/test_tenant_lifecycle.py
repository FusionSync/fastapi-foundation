from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.events import EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxEvent, OutboxEventPublisher, OutboxRepository
from core.permissions import PLATFORM_TENANT_ID, AuthorizationDecision
from core.tenancy import (
    TENANT_CREATED_EVENT,
    TENANT_DELETED_EVENT,
    TENANT_DELETING_EVENT,
    TENANT_REACTIVATED_EVENT,
    TENANT_SUSPENDED_EVENT,
    Tenant,
    TenantLifecyclePolicy,
    TenantLifecycleService,
    TenantMember,
    assert_tenant_operation_allowed,
    is_tenant_operation_allowed,
    validate_tenant_transition,
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


def test_tenant_lifecycle_behavior_matrix() -> None:
    assert is_tenant_operation_allowed("active", "write") is True
    assert is_tenant_operation_allowed("suspended", "read") is True
    assert is_tenant_operation_allowed("suspended", "write") is False
    assert is_tenant_operation_allowed("suspended", "task") is False
    assert is_tenant_operation_allowed("deleting", "login") is False
    assert is_tenant_operation_allowed("deleting", "read") is False
    assert is_tenant_operation_allowed("archived", "read") is False
    assert (
        is_tenant_operation_allowed(
            "archived",
            "read",
            policy=TenantLifecyclePolicy(allow_archived_read=True),
        )
        is True
    )
    assert is_tenant_operation_allowed("deleted", "admin") is True


def test_invalid_lifecycle_transition_is_rejected() -> None:
    with pytest.raises(AppError) as exc_info:
        validate_tenant_transition("active", "deleted")

    assert exc_info.value.code == "TENANT_STATE_FORBIDDEN"


@pytest.mark.asyncio
async def test_provisioning_creates_active_tenant_owner_member_and_created_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        service = TenantLifecycleService(
            uow.session,
            _event_publisher(uow.session, event_registry),
        )
        tenant = await service.provision_tenant(
            tenant_id="tenant-a",
            name="Tenant A",
            code="tenant-a",
            owner_user_id="owner-1",
            actor_id="owner-1",
            request_id="req_test",
            authorization_decision=_tenant_manage_decision(user_id="owner-1"),
        )

    tenants = await _all(session_factory, Tenant)
    members = await _all(session_factory, TenantMember)
    events = await _all(session_factory, OutboxEvent)
    assert tenant.status == "active"
    assert [row.id for row in tenants] == ["tenant-a"]
    assert [(row.tenant_id, row.user_id, row.status) for row in members] == [
        ("tenant-a", "owner-1", "active")
    ]
    assert [(event.event_type, event.payload["status"]) for event in events] == [
        (TENANT_CREATED_EVENT, "active")
    ]


@pytest.mark.asyncio
async def test_suspending_tenant_revokes_sessions_and_blocks_writes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    revoked: list[tuple[str, str]] = []
    tenant = await _create_active_tenant(session_factory)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        current = await uow.session.get(Tenant, tenant.id)
        assert current is not None
        service = TenantLifecycleService(
            uow.session,
            _event_publisher(uow.session, event_registry),
            session_revocation_hook=lambda tenant_id, reason: revoked.append((tenant_id, reason)),
        )
        await service.suspend_tenant(
            current,
            actor_id="admin-1",
            request_id="req_test",
            reason="billing hold",
            authorization_decision=_tenant_manage_decision(user_id="admin-1"),
        )

    suspended = (await _all(session_factory, Tenant))[0]
    events = await _all(session_factory, OutboxEvent)
    assert suspended.status == "suspended"
    assert revoked == [("tenant-a", "billing hold")]
    assert events[-1].event_type == TENANT_SUSPENDED_EVENT
    with pytest.raises(AppError):
        assert_tenant_operation_allowed(
            tenant_id="tenant-a",
            status="suspended",
            operation="write",
        )


@pytest.mark.asyncio
async def test_delete_workflow_enters_deleting_and_emits_outbox_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    revoked: list[tuple[str, str]] = []
    tenant = await _create_active_tenant(session_factory)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        current = await uow.session.get(Tenant, tenant.id)
        assert current is not None
        service = TenantLifecycleService(
            uow.session,
            _event_publisher(uow.session, event_registry),
            session_revocation_hook=lambda tenant_id, reason: revoked.append((tenant_id, reason)),
        )
        await service.begin_delete_tenant(
            current,
            actor_id="admin-1",
            request_id="req_test",
            reason="tenant requested deletion",
            authorization_decision=_tenant_manage_decision(user_id="admin-1"),
        )

    deleting = (await _all(session_factory, Tenant))[0]
    events = await _all(session_factory, OutboxEvent)
    assert deleting.status == "deleting"
    assert revoked == [("tenant-a", "tenant requested deletion")]
    assert events[-1].event_type == TENANT_DELETING_EVENT
    with pytest.raises(AppError):
        assert_tenant_operation_allowed(
            tenant_id="tenant-a",
            status="deleting",
            operation="read",
        )


@pytest.mark.asyncio
async def test_delete_workflow_can_finish_as_deleted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    tenant = await _create_active_tenant(session_factory)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        current = await uow.session.get(Tenant, tenant.id)
        assert current is not None
        service = TenantLifecycleService(
            uow.session,
            _event_publisher(uow.session, event_registry),
        )
        await service.begin_delete_tenant(
            current,
            actor_id="admin-1",
            request_id="req_test",
            reason="cleanup",
            authorization_decision=_tenant_manage_decision(user_id="admin-1"),
        )
        await service.finish_delete_tenant(
            current,
            target="deleted",
            actor_id="admin-1",
            request_id="req_test",
            reason="cleanup complete",
            authorization_decision=_tenant_manage_decision(user_id="admin-1"),
        )

    deleted = (await _all(session_factory, Tenant))[0]
    events = await _all(session_factory, OutboxEvent)
    assert deleted.status == "deleted"
    assert events[-1].event_type == TENANT_DELETED_EVENT


@pytest.mark.asyncio
async def test_tenant_lifecycle_mutation_requires_authorization_decision(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    tenant = await _create_active_tenant(session_factory)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        current = await uow.session.get(Tenant, tenant.id)
        assert current is not None
        service = TenantLifecycleService(
            uow.session,
            _event_publisher(uow.session, event_registry),
        )
        with pytest.raises(AppError) as exc_info:
            await service.suspend_tenant(
                current,
                actor_id="admin-1",
                request_id="req_test",
                reason="billing hold",
            )

    assert exc_info.value.code == "PERMISSION_DENIED"


async def _create_active_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> Tenant:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        tenant = Tenant(
            id="tenant-a",
            name="Tenant A",
            code="tenant-a",
            status="active",
            deployment_mode="local",
        )
        uow.session.add(tenant)
        return tenant


async def _all(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[Tenant] | type[TenantMember] | type[OutboxEvent],
):
    async with session_factory() as session:
        rows = list((await session.execute(select(model))).scalars().all())
        for row in rows:
            session.expunge(row)
        return rows


def _tenant_event_registry() -> EventRegistry:
    registry = EventRegistry()
    for event_type in (
        TENANT_CREATED_EVENT,
        TENANT_SUSPENDED_EVENT,
        TENANT_REACTIVATED_EVENT,
        TENANT_DELETING_EVENT,
        TENANT_DELETED_EVENT,
    ):
        registry.register(event_type, 1, lambda event: None)
    return registry


def _event_publisher(session: AsyncSession, registry: EventRegistry) -> OutboxEventPublisher:
    return OutboxEventPublisher(OutboxRepository(session, registry=registry))


def _tenant_manage_decision(
    *,
    user_id: str = "owner-1",
    tenant_id: str = PLATFORM_TENANT_ID,
    allowed: bool = True,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=allowed,
        tenant_id=tenant_id,
        user_id=user_id,
        resource="tenant",
        action="manage",
        reason="matched_projected_policy" if allowed else "missing_projected_policy",
        policy_version=1 if allowed else None,
    )
