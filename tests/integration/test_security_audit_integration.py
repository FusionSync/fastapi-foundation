from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.events import EventRegistry
from core.outbox import OutboxEvent, OutboxEventPublisher, OutboxRepository
from core.permissions import (
    PLATFORM_TENANT_ID,
    AuthorizationDecision,
    RoleGrant,
    RoleGrantService,
    RoleTemplate,
)
from core.tenancy import TENANT_CREATED_EVENT, Tenant, TenantLifecycleService, TenantMember
from platform_apps.accounts import AccountsService, User
from platform_apps.audit import AuditLog, AuditService


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
async def test_role_grant_writes_strong_audit_with_outbox(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_viewer_template())
        grant = await RoleGrantService(
            uow.session,
            _event_publisher(uow.session, event_registry),
            audit=AuditService(uow.session),
        ).grant_role(
            tenant_id="tenant-a",
            subject_type="user",
            subject_id="user-1",
            role_template_id="template-viewer",
            actor_id="owner-1",
            request_id="req-grant",
            authorization_decision=_role_grant_decision(),
            reason="onboard owner",
        )

    audit_logs = await _audit_logs(session_factory)
    assert await _count(session_factory, RoleGrant) == 1
    assert await _count(session_factory, OutboxEvent) == 1
    assert len(audit_logs) == 1
    assert audit_logs[0].action == "role.granted"
    assert audit_logs[0].tenant_id == "tenant-a"
    assert audit_logs[0].actor_id == "owner-1"
    assert audit_logs[0].resource_type == "role_grant"
    assert audit_logs[0].resource_id == grant.id
    assert audit_logs[0].reason == "onboard owner"
    assert audit_logs[0].request_id == "req-grant"
    assert audit_logs[0].policy_version == 1
    assert audit_logs[0].payload == {
        "subject_type": "user",
        "subject_id": "user-1",
        "role_template_id": "template-viewer",
    }


@pytest.mark.asyncio
async def test_role_grant_audit_rolls_back_with_business_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    with pytest.raises(RuntimeError, match="rollback role grant"):
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(_viewer_template())
            await RoleGrantService(
                uow.session,
                _event_publisher(uow.session, event_registry),
                audit=AuditService(uow.session),
            ).grant_role(
                tenant_id="tenant-a",
                subject_type="user",
                subject_id="user-1",
                role_template_id="template-viewer",
                actor_id="owner-1",
                request_id="req-grant",
                authorization_decision=_role_grant_decision(),
                reason="test rollback",
            )
            raise RuntimeError("rollback role grant")

    assert await _count(session_factory, RoleGrant) == 0
    assert await _count(session_factory, AuditLog) == 0
    assert await _count(session_factory, OutboxEvent) == 0


@pytest.mark.asyncio
async def test_role_revoke_writes_strong_audit_with_outbox(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_viewer_template())
        grant = await RoleGrantService(
            uow.session,
            _event_publisher(uow.session, event_registry),
        ).grant_role(
            tenant_id="tenant-a",
            subject_type="user",
            subject_id="user-1",
            role_template_id="template-viewer",
            actor_id="owner-1",
            request_id="req-grant",
            authorization_decision=_role_grant_decision(),
        )
        grant_id = grant.id

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await RoleGrantService(
            uow.session,
            _event_publisher(uow.session, event_registry),
            audit=AuditService(uow.session),
        ).revoke_role(
            grant_id=grant_id,
            actor_id="owner-1",
            request_id="req-revoke",
            authorization_decision=_role_grant_decision(),
            reason="user left tenant",
        )

    audit_logs = await _audit_logs(session_factory)
    assert await _count(session_factory, RoleGrant) == 0
    assert await _count(session_factory, OutboxEvent) == 2
    assert len(audit_logs) == 1
    assert audit_logs[0].action == "role.revoked"
    assert audit_logs[0].tenant_id == "tenant-a"
    assert audit_logs[0].actor_id == "owner-1"
    assert audit_logs[0].resource_type == "role_grant"
    assert audit_logs[0].resource_id == grant_id
    assert audit_logs[0].reason == "user left tenant"
    assert audit_logs[0].request_id == "req-revoke"
    assert audit_logs[0].policy_version == 1
    assert audit_logs[0].payload == {
        "subject_type": "user",
        "subject_id": "user-1",
        "role_template_id": "template-viewer",
    }


@pytest.mark.asyncio
async def test_disable_user_writes_security_audit_and_revokes_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        accounts = AccountsService(uow.session, audit=AuditService(uow.session))
        user = await accounts.create_user(
            email="owner@example.com",
            display_name="Owner",
        )
        uow.session.add(
            Tenant(
                id="tenant-a",
                code="tenant-a",
                name="Tenant A",
                status="active",
                deployment_mode="local",
            )
        )
        uow.session.add(TenantMember(tenant_id="tenant-a", user_id=user.id, status="active"))
        await accounts.create_session(
            user_id=user.id,
            tenant_id="tenant-a",
            auth_provider="local",
        )
        await accounts.disable_user(
            user.id,
            reason="security incident",
            actor_id="admin-1",
            request_id="req-disable",
            authorization_decision=_user_manage_decision(user_id="admin-1"),
        )

    disabled_user = await _user(session_factory, user.id)
    audit_logs = await _audit_logs(session_factory)
    assert disabled_user.status == "disabled"
    assert disabled_user.token_version == 2
    assert [audit_log.action for audit_log in audit_logs] == [
        "session.created",
        "user.disabled",
    ]
    user_disabled_audit = audit_logs[1]
    assert user_disabled_audit.actor_id == "admin-1"
    assert user_disabled_audit.resource_type == "user"
    assert user_disabled_audit.resource_id == user.id
    assert user_disabled_audit.reason == "security incident"
    assert user_disabled_audit.request_id == "req-disable"
    assert user_disabled_audit.payload == {"revoked_sessions": 1, "token_version": 2}


@pytest.mark.asyncio
async def test_tenant_suspend_writes_lifecycle_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(
            Tenant(
                id="tenant-a",
                code="tenant-a",
                name="Tenant A",
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
            _event_publisher(uow.session, event_registry),
            audit=AuditService(uow.session),
        ).suspend_tenant(
            tenant,
            actor_id="admin-1",
            request_id="req-suspend",
            reason="billing hold",
            authorization_decision=_tenant_manage_decision(user_id="admin-1"),
        )

    audit_logs = await _audit_logs(session_factory)
    assert len(audit_logs) == 1
    assert audit_logs[0].action == "tenant.suspended"
    assert audit_logs[0].tenant_id == "tenant-a"
    assert audit_logs[0].actor_id == "admin-1"
    assert audit_logs[0].resource_type == "tenant"
    assert audit_logs[0].resource_id == "tenant-a"
    assert audit_logs[0].reason == "billing hold"
    assert audit_logs[0].request_id == "req-suspend"
    assert audit_logs[0].payload == {
        "from_status": "active",
        "to_status": "suspended",
        "event_type": "tenant.suspended",
        "revoke_sessions": True,
    }


@pytest.mark.asyncio
async def test_tenant_provision_writes_lifecycle_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        tenant = await TenantLifecycleService(
            uow.session,
            _event_publisher(uow.session, event_registry),
            audit=AuditService(uow.session),
        ).provision_tenant(
            tenant_id="tenant-a",
            name="Tenant A",
            code="tenant-a",
            owner_user_id="owner-1",
            actor_id="owner-1",
            request_id="req-provision",
            authorization_decision=_tenant_manage_decision(user_id="owner-1"),
        )

    audit_logs = await _audit_logs(session_factory)
    assert tenant.status == "active"
    assert len(audit_logs) == 1
    assert audit_logs[0].action == TENANT_CREATED_EVENT
    assert audit_logs[0].tenant_id == "tenant-a"
    assert audit_logs[0].actor_id == "owner-1"
    assert audit_logs[0].resource_type == "tenant"
    assert audit_logs[0].resource_id == "tenant-a"
    assert audit_logs[0].request_id == "req-provision"
    assert audit_logs[0].payload == {
        "from_status": "provisioning",
        "to_status": "active",
        "event_type": TENANT_CREATED_EVENT,
        "revoke_sessions": False,
        "owner_user_id": "owner-1",
    }


@pytest.mark.asyncio
async def test_tenant_reactivate_writes_lifecycle_audit_and_outbox_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _tenant_event_registry()
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(
            Tenant(
                id="tenant-a",
                code="tenant-a",
                name="Tenant A",
                status="suspended",
                deployment_mode="local",
            )
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        tenant = await uow.session.get(Tenant, "tenant-a")
        assert tenant is not None
        await TenantLifecycleService(
            uow.session,
            _event_publisher(uow.session, event_registry),
            audit=AuditService(uow.session),
        ).reactivate_tenant(
            tenant,
            actor_id="admin-1",
            request_id="req-reactivate",
            reason="billing cleared",
            authorization_decision=_tenant_manage_decision(user_id="admin-1"),
        )

    audit_logs = await _audit_logs(session_factory)
    outbox_events = await _outbox_events(session_factory)
    assert len(audit_logs) == 1
    assert audit_logs[0].action == "tenant.reactivated"
    assert audit_logs[0].tenant_id == "tenant-a"
    assert audit_logs[0].actor_id == "admin-1"
    assert audit_logs[0].resource_type == "tenant"
    assert audit_logs[0].resource_id == "tenant-a"
    assert audit_logs[0].reason == "billing cleared"
    assert audit_logs[0].request_id == "req-reactivate"
    assert audit_logs[0].payload == {
        "from_status": "suspended",
        "to_status": "active",
        "event_type": "tenant.reactivated",
        "revoke_sessions": False,
    }
    assert [(event.event_type, event.payload["status"]) for event in outbox_events] == [
        ("tenant.reactivated", "active")
    ]


def _tenant_event_registry() -> EventRegistry:
    registry = EventRegistry()
    registry.register("tenant.created", 1, lambda event: None)
    registry.register("tenant.reactivated", 1, lambda event: None)
    registry.register("permissions.role_grant_changed", 1, lambda event: None)
    registry.register("tenant.suspended", 1, lambda event: None)
    return registry


def _event_publisher(session: AsyncSession, registry: EventRegistry) -> OutboxEventPublisher:
    return OutboxEventPublisher(OutboxRepository(session, registry=registry))


def _viewer_template() -> RoleTemplate:
    return RoleTemplate(
        id="template-viewer",
        scope="tenant",
        name="viewer",
        version=1,
        permissions=[{"resource": "example", "action": "read"}],
    )


def _role_grant_decision() -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        tenant_id="tenant-a",
        user_id="owner-1",
        resource="role_grant",
        action="manage",
        reason="matched_projected_policy",
        policy_version=1,
    )


def _user_manage_decision(*, user_id: str) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        tenant_id=PLATFORM_TENANT_ID,
        user_id=user_id,
        resource="user",
        action="manage",
        reason="matched_projected_policy",
        policy_version=1,
    )


def _tenant_manage_decision(*, user_id: str) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        tenant_id=PLATFORM_TENANT_ID,
        user_id=user_id,
        resource="tenant",
        action="manage",
        reason="matched_projected_policy",
        policy_version=1,
    )


async def _audit_logs(session_factory: async_sessionmaker[AsyncSession]) -> list[AuditLog]:
    async with session_factory() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.created_at))
        audit_logs = list(result.scalars().all())
        for audit_log in audit_logs:
            session.expunge(audit_log)
        return audit_logs


async def _user(session_factory: async_sessionmaker[AsyncSession], user_id: str) -> User:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        session.expunge(user)
        return user


async def _count(session_factory: async_sessionmaker[AsyncSession], model: type[object]) -> int:
    async with session_factory() as session:
        result = await session.scalar(select(func.count()).select_from(model))
        return int(result or 0)


async def _outbox_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[OutboxEvent]:
    async with session_factory() as session:
        result = await session.execute(select(OutboxEvent).order_by(OutboxEvent.created_at))
        events = list(result.scalars().all())
        for event in events:
            session.expunge(event)
        return events
