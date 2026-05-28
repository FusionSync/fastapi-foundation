from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.exceptions import AppError
from core.permissions import AuthorizationService, ProjectedPolicy
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
async def test_authorize_allows_matching_projected_policy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(
            ProjectedPolicy(
                tenant_id="tenant-a",
                subject="user:user-1",
                resource="workspace",
                action="read",
                effect="allow",
                role_grant_id="grant-1",
                policy_version=3,
            )
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        decision = await AuthorizationService(uow.session).authorize(
            user_id="user-1",
            tenant_id="tenant-a",
            resource="workspace",
            action="read",
            request_id="req-1",
        )

    assert decision.allowed is True
    assert decision.policy_version == 3
    assert decision.reason == "matched_projected_policy"
    assert await _audit_logs(session_factory) == []


@pytest.mark.asyncio
async def test_authorize_denies_cross_tenant_and_writes_security_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(
            ProjectedPolicy(
                tenant_id="tenant-a",
                subject="user:user-1",
                resource="workspace",
                action="read",
                effect="allow",
                role_grant_id="grant-1",
                policy_version=3,
            )
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        decision = await AuthorizationService(
            uow.session,
            audit=AuditService(uow.session),
        ).authorize(
            user_id="user-1",
            tenant_id="tenant-b",
            resource="workspace",
            action="read",
            resource_id="workspace-1",
            request_id="req-2",
        )

    audit_logs = await _audit_logs(session_factory)
    assert decision.allowed is False
    assert decision.policy_version is None
    assert decision.reason == "missing_projected_policy"
    assert len(audit_logs) == 1
    assert audit_logs[0].action == "authorization.denied"
    assert audit_logs[0].result == "denied"
    assert audit_logs[0].tenant_id == "tenant-b"
    assert audit_logs[0].actor_id == "user-1"
    assert audit_logs[0].resource_type == "workspace"
    assert audit_logs[0].resource_id == "workspace-1"
    assert audit_logs[0].request_id == "req-2"
    assert audit_logs[0].payload == {
        "resource": "workspace",
        "action": "read",
        "subject": "user:user-1",
        "reason": "missing_projected_policy",
    }


@pytest.mark.asyncio
async def test_require_authorized_raises_after_recording_denied_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as exc_info:
            await AuthorizationService(
                uow.session,
                audit=AuditService(uow.session),
            ).require(
                user_id="user-1",
                tenant_id="tenant-a",
                resource="workspace",
                action="write",
                request_id="req-3",
            )

    audit_logs = await _audit_logs(session_factory)
    assert exc_info.value.code == "PERMISSION_DENIED"
    assert exc_info.value.details == {
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "resource": "workspace",
        "action": "write",
    }
    assert len(audit_logs) == 1
    assert audit_logs[0].action == "authorization.denied"


async def _audit_logs(session_factory: async_sessionmaker[AsyncSession]) -> list[AuditLog]:
    async with session_factory() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.created_at))
        audit_logs = list(result.scalars().all())
        for audit_log in audit_logs:
            session.expunge(audit_log)
        return audit_logs
