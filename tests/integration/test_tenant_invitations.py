from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import Model
from core.cache import (
    CacheInvalidationHandler,
    MemoryCacheProvider,
    permission_subject_cache_key,
    register_cache_invalidation_handlers,
    tenant_membership_cache_key,
)
from core.db import unit_of_work
from core.events import EventEnvelope, EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxDispatcher, OutboxEvent, OutboxEventPublisher, OutboxRepository
from core.permissions import (
    ROLE_GRANT_CHANGED_EVENT,
    AuthorizationDecision,
    PolicyProjector,
    ProjectedPolicy,
    RoleGrant,
    RoleTemplate,
)
from core.tenancy import TENANT_MEMBER_ACTIVATED_EVENT, Tenant, TenantMember
from platform_apps import tenants as tenants_app

TENANT_INVITATION_ACCEPTED_EVENT = "tenant.invitation_accepted"
TENANT_INVITATION_ISSUED_EVENT = "tenant.invitation_issued"


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Model.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_invitation_acceptance_creates_member_role_grant_and_projection(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _invitation_event_registry()
    token: str

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_tenant())
        uow.session.add(_viewer_template())
        issued = await tenants_app.TenantInvitationService(
            uow.session,
            _event_publisher(uow.session, event_registry),
        ).issue_invitation(
            tenant_id="tenant-a",
            email="New.User@Example.com",
            role_template_id="template-viewer",
            actor_id="owner-1",
            request_id="req_invite",
            expires_at=datetime.now(UTC) + timedelta(days=7),
            authorization_decision=_invitation_decision(action="invite"),
            role_grant_authorization_decision=_role_grant_decision(),
        )
        token = issued.token

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        invitation = await tenants_app.TenantInvitationService(
            uow.session,
            _event_publisher(uow.session, event_registry),
        ).accept_invitation(
            token=token,
            user_id="user-2",
            email="new.user@example.com",
            actor_id="user-2",
            request_id="req_accept",
        )

    dispatch_session: AsyncSession | None = None

    async def handle_role_grant_changed(envelope: EventEnvelope) -> None:
        assert dispatch_session is not None
        await PolicyProjector(dispatch_session).handle_role_grant_changed(envelope)

    event_registry.register(ROLE_GRANT_CHANGED_EVENT, 1, handle_role_grant_changed)
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        dispatch_session = uow.session
        stats = await OutboxDispatcher(
            OutboxRepository(uow.session, registry=event_registry),
            event_registry,
            dispatcher_id="tenant-invitation-test",
            batch_size=10,
        ).dispatch_once()
        dispatch_session = None

    invitations = await _all(session_factory, tenants_app.TenantInvitation)
    members = await _all(session_factory, TenantMember)
    grants = await _all(session_factory, RoleGrant)
    policies = await _all(session_factory, ProjectedPolicy)
    events = await _all(session_factory, OutboxEvent)

    assert invitation.status == "accepted"
    assert invitation.accepted_by_user_id == "user-2"
    assert invitations[0].token_hash != token
    assert [(member.tenant_id, member.user_id, member.status) for member in members] == [
        ("tenant-a", "user-2", "active")
    ]
    assert [(grant.tenant_id, grant.subject_id, grant.role_template_id) for grant in grants] == [
        ("tenant-a", "user-2", "template-viewer")
    ]
    assert [(policy.subject, policy.resource, policy.action) for policy in policies] == [
        ("user:user-2", "example", "read")
    ]
    assert stats.published == 4
    assert [event.event_type for event in events] == [
        TENANT_INVITATION_ISSUED_EVENT,
        TENANT_MEMBER_ACTIVATED_EVENT,
        ROLE_GRANT_CHANGED_EVENT,
        TENANT_INVITATION_ACCEPTED_EVENT,
    ]


@pytest.mark.asyncio
async def test_invitation_acceptance_invalidates_membership_and_permission_cache(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cache = MemoryCacheProvider()
    await cache.set(
        tenant_membership_cache_key("tenant-a", "user-2"),
        "stale-membership",
        permanent=True,
    )
    await cache.set(
        permission_subject_cache_key("tenant-a", "user", "user-2"),
        "stale-user-policy",
        permanent=True,
    )
    token: str
    event_registry = _invitation_event_registry()
    register_cache_invalidation_handlers(
        event_registry,
        CacheInvalidationHandler(cache),
    )
    dispatch_session: AsyncSession | None = None

    async def handle_role_grant_changed(envelope: EventEnvelope) -> None:
        assert dispatch_session is not None
        await PolicyProjector(dispatch_session).handle_role_grant_changed(envelope)

    event_registry.register(
        ROLE_GRANT_CHANGED_EVENT,
        1,
        handle_role_grant_changed,
        handler_key="test.permission-projector",
    )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_tenant())
        uow.session.add(_viewer_template())
        issued = await tenants_app.TenantInvitationService(
            uow.session,
            _event_publisher(uow.session, event_registry),
        ).issue_invitation(
            tenant_id="tenant-a",
            email="New.User@Example.com",
            role_template_id="template-viewer",
            actor_id="owner-1",
            request_id="req_invite",
            expires_at=datetime.now(UTC) + timedelta(days=7),
            authorization_decision=_invitation_decision(action="invite"),
            role_grant_authorization_decision=_role_grant_decision(),
        )
        token = issued.token

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await tenants_app.TenantInvitationService(
            uow.session,
            _event_publisher(uow.session, event_registry),
        ).accept_invitation(
            token=token,
            user_id="user-2",
            email="new.user@example.com",
            actor_id="user-2",
            request_id="req_accept",
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        dispatch_session = uow.session
        stats = await OutboxDispatcher(
            OutboxRepository(uow.session, registry=event_registry),
            event_registry,
            dispatcher_id="tenant-membership-cache-test",
            batch_size=10,
        ).dispatch_once()
        dispatch_session = None

    policies = await _all(session_factory, ProjectedPolicy)
    events = await _all(session_factory, OutboxEvent)
    assert stats.published == 4
    assert await cache.get(tenant_membership_cache_key("tenant-a", "user-2")) is None
    assert await cache.get(permission_subject_cache_key("tenant-a", "user", "user-2")) is None
    assert [(policy.subject, policy.resource, policy.action) for policy in policies] == [
        ("user:user-2", "example", "read")
    ]
    assert [event.event_type for event in events] == [
        TENANT_INVITATION_ISSUED_EVENT,
        TENANT_MEMBER_ACTIVATED_EVENT,
        ROLE_GRANT_CHANGED_EVENT,
        TENANT_INVITATION_ACCEPTED_EVENT,
    ]


@pytest.mark.asyncio
async def test_initial_role_invitation_requires_role_grant_authorization(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _invitation_event_registry()

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_tenant())
        with pytest.raises(AppError) as exc_info:
            await tenants_app.TenantInvitationService(
                uow.session,
                _event_publisher(uow.session, event_registry),
            ).issue_invitation(
                tenant_id="tenant-a",
                email="new.user@example.com",
                role_template_id="template-viewer",
                actor_id="owner-1",
                request_id="req_invite",
                expires_at=datetime.now(UTC) + timedelta(days=7),
                authorization_decision=_invitation_decision(action="invite"),
            )

    assert exc_info.value.code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_accepting_expired_invitation_marks_it_expired(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _invitation_event_registry()
    token: str
    expires_at = datetime.now(UTC) + timedelta(days=1)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_tenant())
        issued = await tenants_app.TenantInvitationService(
            uow.session,
            _event_publisher(uow.session, event_registry),
        ).issue_invitation(
            tenant_id="tenant-a",
            email="new.user@example.com",
            role_template_id=None,
            actor_id="owner-1",
            request_id="req_invite",
            expires_at=expires_at,
            authorization_decision=_invitation_decision(action="invite"),
        )
        token = issued.token

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as exc_info:
            await tenants_app.TenantInvitationService(
                uow.session,
                _event_publisher(uow.session, event_registry),
            ).accept_invitation(
                token=token,
                user_id="user-2",
                email="new.user@example.com",
                actor_id="user-2",
                request_id="req_accept",
                accepted_at=expires_at + timedelta(days=1),
            )

    invitations = await _all(session_factory, tenants_app.TenantInvitation)
    assert exc_info.value.code == "VALIDATION_ERROR"
    assert exc_info.value.status_code == 400
    assert invitations[0].status == "expired"


@pytest.mark.asyncio
async def test_initial_role_invitation_rejects_missing_role_template(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_registry = _invitation_event_registry()

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(_tenant())
        with pytest.raises(AppError) as exc_info:
            await tenants_app.TenantInvitationService(
                uow.session,
                _event_publisher(uow.session, event_registry),
            ).issue_invitation(
                tenant_id="tenant-a",
                email="new.user@example.com",
                role_template_id="missing-template",
                actor_id="owner-1",
                request_id="req_invite",
                expires_at=datetime.now(UTC) + timedelta(days=7),
                authorization_decision=_invitation_decision(action="invite"),
                role_grant_authorization_decision=_role_grant_decision(),
            )

    assert exc_info.value.code == "NOT_FOUND"
    assert exc_info.value.status_code == 404


def _tenant() -> Tenant:
    return Tenant(
        id="tenant-a",
        name="Tenant A",
        code="tenant-a",
        status="active",
        deployment_mode="local",
    )


def _viewer_template() -> RoleTemplate:
    return RoleTemplate(
        id="template-viewer",
        scope="tenant",
        name="viewer",
        version=1,
        permissions=[{"resource": "example", "action": "read"}],
    )


def _invitation_event_registry() -> EventRegistry:
    registry = EventRegistry()
    registry.register(TENANT_INVITATION_ISSUED_EVENT, 1, lambda event: None)
    registry.register(TENANT_INVITATION_ACCEPTED_EVENT, 1, lambda event: None)
    registry.register(TENANT_MEMBER_ACTIVATED_EVENT, 1, lambda event: None)
    registry.register(ROLE_GRANT_CHANGED_EVENT, 1, lambda event: None)
    return registry


def _event_publisher(session: AsyncSession, registry: EventRegistry) -> OutboxEventPublisher:
    return OutboxEventPublisher(OutboxRepository(session, registry=registry))


def _invitation_decision(*, action: str, user_id: str = "owner-1") -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        tenant_id="tenant-a",
        user_id=user_id,
        resource="tenant_invitation",
        action=action,
        reason="matched_projected_policy",
        policy_version=1,
    )


def _role_grant_decision(*, user_id: str = "owner-1") -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        tenant_id="tenant-a",
        user_id=user_id,
        resource="role_grant",
        action="grant",
        reason="matched_projected_policy",
        policy_version=1,
    )


async def _all(
    session_factory: async_sessionmaker[AsyncSession],
    model: type,
):
    async with session_factory() as session:
        rows = list((await session.execute(select(model))).scalars().all())
        for row in rows:
            session.expunge(row)
        return rows
