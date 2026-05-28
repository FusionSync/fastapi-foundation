from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.events import EventEnvelope, EventRegistry
from core.outbox import OutboxDispatcher, OutboxEvent, OutboxRepository
from core.permissions import (
    ROLE_GRANT_CHANGED_EVENT,
    PermissionCache,
    PolicyProjector,
    ProjectedPolicy,
    RoleGrant,
    RoleGrantService,
    RoleTemplate,
)
from core.tenancy import TenantMember


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
async def test_role_grant_outbox_event_updates_projected_policy_and_cache(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cache = PermissionCache()
    dispatch_session: AsyncSession | None = None

    async def handle_role_grant_changed(envelope: EventEnvelope) -> None:
        assert dispatch_session is not None
        await PolicyProjector(dispatch_session, cache=cache).handle_role_grant_changed(envelope)

    event_registry = EventRegistry()
    event_registry.register(ROLE_GRANT_CHANGED_EVENT, 1, handle_role_grant_changed)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_viewer_template())
        outbox = OutboxRepository(uow.session, registry=event_registry)
        await RoleGrantService(uow.session, outbox).grant_role(
            tenant_id="tenant-a",
            subject_type="user",
            subject_id="user-1",
            role_template_id="template-viewer",
            actor_id="owner-1",
            request_id="req_test",
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        dispatch_session = uow.session
        dispatcher = OutboxDispatcher(
            OutboxRepository(uow.session, registry=event_registry),
            event_registry,
            dispatcher_id="permission-projector",
        )
        stats = await dispatcher.dispatch_once()
        dispatch_session = None

    policies = await _policies(session_factory)
    event = await _first_outbox_event(session_factory)
    assert stats.published == 1
    assert cache.version == 1
    assert event.status == "published"
    assert [policy.resource for policy in policies] == ["example"]
    assert [policy.action for policy in policies] == ["read"]
    assert [policy.subject for policy in policies] == ["user:user-1"]


@pytest.mark.asyncio
async def test_role_revoke_outbox_event_removes_projected_policy_and_cache(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cache = PermissionCache()
    dispatch_session: AsyncSession | None = None

    async def handle_role_grant_changed(envelope: EventEnvelope) -> None:
        assert dispatch_session is not None
        await PolicyProjector(dispatch_session, cache=cache).handle_role_grant_changed(envelope)

    event_registry = EventRegistry()
    event_registry.register(ROLE_GRANT_CHANGED_EVENT, 1, handle_role_grant_changed)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_viewer_template())
        grant = await RoleGrantService(
            uow.session,
            OutboxRepository(uow.session, registry=event_registry),
        ).grant_role(
            tenant_id="tenant-a",
            subject_type="user",
            subject_id="user-1",
            role_template_id="template-viewer",
            actor_id="owner-1",
            request_id="req_grant",
        )
        grant_id = grant.id

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        dispatch_session = uow.session
        await OutboxDispatcher(
            OutboxRepository(uow.session, registry=event_registry),
            event_registry,
            dispatcher_id="permission-projector",
        ).dispatch_once()
        dispatch_session = None

    assert len(await _policies(session_factory)) == 1
    assert cache.version == 1

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await RoleGrantService(
            uow.session,
            OutboxRepository(uow.session, registry=event_registry),
        ).revoke_role(
            grant_id=grant_id,
            actor_id="owner-1",
            request_id="req_revoke",
            reason="user left tenant",
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        dispatch_session = uow.session
        await OutboxDispatcher(
            OutboxRepository(uow.session, registry=event_registry),
            event_registry,
            dispatcher_id="permission-projector",
        ).dispatch_once()
        dispatch_session = None

    assert await _grant_count(session_factory) == 0
    assert await _policies(session_factory) == []
    assert cache.version == 2


@pytest.mark.asyncio
async def test_role_revoke_removes_projected_policy_before_outbox_dispatch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = EventRegistry()
    event_registry.register(ROLE_GRANT_CHANGED_EVENT, 1, lambda event: None)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_viewer_template())
        grant = await RoleGrantService(
            uow.session,
            OutboxRepository(uow.session, registry=event_registry),
        ).grant_role(
            tenant_id="tenant-a",
            subject_type="user",
            subject_id="user-1",
            role_template_id="template-viewer",
            actor_id="owner-1",
            request_id="req_grant",
        )
        await PolicyProjector(uow.session).project_grant(grant, _viewer_template())
        grant_id = grant.id

    assert len(await _policies(session_factory)) == 1

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await RoleGrantService(
            uow.session,
            OutboxRepository(uow.session, registry=event_registry),
        ).revoke_role(
            grant_id=grant_id,
            actor_id="owner-1",
            request_id="req_revoke",
            reason="user left tenant",
        )

    assert await _policies(session_factory) == []


@pytest.mark.asyncio
async def test_permission_reconciliation_detects_and_repairs_missing_policy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_viewer_template())
        uow.session.add(
            RoleGrant(
                id="grant-1",
                tenant_id="tenant-a",
                subject_type="user",
                subject_id="user-1",
                role_template_id="template-viewer",
                policy_version=1,
            )
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        projector = PolicyProjector(uow.session)
        result = await projector.reconcile(repair=False)

    assert result.ok is False
    assert len(result.missing) == 1

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        projector = PolicyProjector(uow.session)
        repaired = await projector.reconcile(repair=True)

    assert repaired.repaired is True
    assert repaired.ok is True
    assert len(await _policies(session_factory)) == 1


@pytest.mark.asyncio
async def test_permission_reconciliation_repairs_incrementally_and_detects_version_drift(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_viewer_template())
        uow.session.add_all(
            [
                RoleGrant(
                    id="grant-current",
                    tenant_id="tenant-a",
                    subject_type="user",
                    subject_id="user-current",
                    role_template_id="template-viewer",
                    policy_version=1,
                ),
                RoleGrant(
                    id="grant-drifted",
                    tenant_id="tenant-a",
                    subject_type="user",
                    subject_id="user-drifted",
                    role_template_id="template-viewer",
                    policy_version=2,
                ),
                RoleGrant(
                    id="grant-missing",
                    tenant_id="tenant-a",
                    subject_type="user",
                    subject_id="user-missing",
                    role_template_id="template-viewer",
                    policy_version=1,
                ),
                ProjectedPolicy(
                    id="policy-current",
                    tenant_id="tenant-a",
                    subject="user:user-current",
                    resource="example",
                    action="read",
                    effect="allow",
                    role_grant_id="grant-current",
                    policy_version=1,
                ),
                ProjectedPolicy(
                    id="policy-drifted-old-version",
                    tenant_id="tenant-a",
                    subject="user:user-drifted",
                    resource="example",
                    action="read",
                    effect="allow",
                    role_grant_id="grant-drifted",
                    policy_version=1,
                ),
                ProjectedPolicy(
                    id="policy-stale",
                    tenant_id="tenant-a",
                    subject="user:user-stale",
                    resource="example",
                    action="read",
                    effect="allow",
                    role_grant_id="grant-stale",
                    policy_version=1,
                ),
            ]
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        result = await PolicyProjector(uow.session).reconcile(repair=False)

    assert result.ok is False
    assert sorted(rule.role_grant_id for rule in result.missing) == [
        "grant-drifted",
        "grant-missing",
    ]
    assert sorted(rule.role_grant_id for rule in result.stale) == [
        "grant-drifted",
        "grant-stale",
    ]

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        repaired = await PolicyProjector(uow.session).reconcile(repair=True)

    policies = await _policies(session_factory)
    policy_ids = {policy.id for policy in policies}
    versions_by_grant = {policy.role_grant_id: policy.policy_version for policy in policies}
    assert repaired.repaired is True
    assert repaired.ok is True
    assert "policy-current" in policy_ids
    assert "policy-drifted-old-version" not in policy_ids
    assert "policy-stale" not in policy_ids
    assert versions_by_grant == {
        "grant-current": 1,
        "grant-drifted": 2,
        "grant-missing": 1,
    }


def test_tenant_member_has_no_role_fact_columns() -> None:
    role_like_columns = {"role", "roles", "role_id", "role_template_id"}

    assert role_like_columns.isdisjoint(TenantMember.__table__.columns.keys())


@pytest.mark.asyncio
async def test_projected_policies_authorize_by_tenant_domain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_viewer_template())
        grant = RoleGrant(
            id="grant-tenant-a",
            tenant_id="tenant-a",
            subject_type="user",
            subject_id="user-1",
            role_template_id="template-viewer",
            policy_version=1,
        )
        uow.session.add(grant)
        await PolicyProjector(uow.session).project_grant(grant, _viewer_template())

    async with session_factory() as session:
        allowed = await session.scalar(
            select(func.count()).select_from(ProjectedPolicy).where(
                ProjectedPolicy.tenant_id == "tenant-a",
                ProjectedPolicy.subject == "user:user-1",
                ProjectedPolicy.resource == "example",
                ProjectedPolicy.action == "read",
            )
        )
        denied = await session.scalar(
            select(func.count()).select_from(ProjectedPolicy).where(
                ProjectedPolicy.tenant_id == "tenant-b",
                ProjectedPolicy.subject == "user:user-1",
                ProjectedPolicy.resource == "example",
                ProjectedPolicy.action == "read",
            )
        )

    assert allowed == 1
    assert denied == 0


def _viewer_template() -> RoleTemplate:
    return RoleTemplate(
        id="template-viewer",
        scope="tenant",
        name="viewer",
        version=1,
        permissions=[{"resource": "example", "action": "read"}],
    )


async def _policies(session_factory: async_sessionmaker[AsyncSession]) -> list[ProjectedPolicy]:
    async with session_factory() as session:
        result = await session.execute(select(ProjectedPolicy))
        policies = list(result.scalars().all())
        for policy in policies:
            session.expunge(policy)
        return policies


async def _first_outbox_event(session_factory: async_sessionmaker[AsyncSession]) -> OutboxEvent:
    async with session_factory() as session:
        event = (await session.execute(select(OutboxEvent).limit(1))).scalars().one()
        session.expunge(event)
        return event


async def _grant_count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        result = await session.scalar(select(func.count()).select_from(RoleGrant))
        return int(result or 0)
