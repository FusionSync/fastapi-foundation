from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.context import RequestContext, reset_current_context, set_current_context
from core.db import unit_of_work
from core.exceptions import AppError
from platform_apps.audit import AuditLog, AuditService, audit_hash


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
async def test_audit_record_uses_context_redacts_payload_and_sets_hash_chain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token = set_current_context(
        RequestContext(
            request_id="req-1",
            user_id="user-1",
            tenant_id="tenant-a",
            ip_address="127.0.0.1",
            user_agent="pytest",
        ).freeze()
    )
    try:
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            first = await AuditService(uow.session).record(
                action="permission.denied",
                resource_type="workspace",
                resource_id="workspace-1",
                result="denied",
                reason="missing workspace.write",
                policy_version=7,
                payload={
                    "resource": "workspace",
                    "password": "secret",
                    "nested": {"access_token": "token-value"},
                },
            )
            second = await AuditService(uow.session).record(
                action="permission.denied",
                resource_type="workspace",
                resource_id="workspace-2",
                result="denied",
                reason="missing workspace.read",
                policy_version=8,
                payload={"authorization": "Bearer token"},
            )
    finally:
        reset_current_context(token)

    audit_logs = await _audit_logs(session_factory)
    assert [log.id for log in audit_logs] == [first.id, second.id]
    assert audit_logs[0].tenant_id == "tenant-a"
    assert audit_logs[0].actor_id == "user-1"
    assert audit_logs[0].request_id == "req-1"
    assert audit_logs[0].ip_address == "127.0.0.1"
    assert audit_logs[0].user_agent == "pytest"
    assert audit_logs[0].payload["password"] == "***REDACTED***"
    assert audit_logs[0].payload["nested"] == {"access_token": "***REDACTED***"}
    assert audit_logs[0].hash_prev is None
    assert audit_logs[0].hash == audit_hash(audit_logs[0])
    assert audit_logs[1].hash_prev == audit_logs[0].hash
    assert audit_logs[1].hash == audit_hash(audit_logs[1])


@pytest.mark.asyncio
async def test_security_critical_audit_rolls_back_with_business_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(RuntimeError, match="business rollback"):
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            await AuditService(uow.session).record(
                tenant_id="tenant-a",
                actor_id="user-1",
                action="tenant.suspend",
                resource_type="tenant",
                resource_id="tenant-a",
                result="success",
                reason="test rollback",
                payload={"api_key": "secret"},
            )
            raise RuntimeError("business rollback")

    assert await _audit_count(session_factory) == 0


@pytest.mark.asyncio
async def test_audit_hash_chain_is_partitioned_by_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        tenant_a_first = await AuditService(uow.session).record(
            tenant_id="tenant-a",
            action="tenant-a.first",
            resource_type="tenant",
            resource_id="tenant-a-first",
            result="success",
        )
        tenant_b_first = await AuditService(uow.session).record(
            tenant_id="tenant-b",
            action="tenant-b.first",
            resource_type="tenant",
            resource_id="tenant-b-first",
            result="success",
        )
        tenant_a_second = await AuditService(uow.session).record(
            tenant_id="tenant-a",
            action="tenant-a.second",
            resource_type="tenant",
            resource_id="tenant-a-second",
            result="success",
        )

    audit_logs = {log.resource_id: log for log in await _audit_logs(session_factory)}

    assert audit_logs["tenant-a-first"].hash_prev is None
    assert audit_logs["tenant-b-first"].hash_prev is None
    assert audit_logs["tenant-a-second"].hash_prev == audit_logs["tenant-a-first"].hash
    assert tenant_a_first.hash == audit_logs["tenant-a-first"].hash
    assert tenant_b_first.hash == audit_logs["tenant-b-first"].hash
    assert tenant_a_second.hash == audit_logs["tenant-a-second"].hash


@pytest.mark.asyncio
async def test_audit_hash_chain_verifier_detects_tampering(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await AuditService(uow.session).record(
            tenant_id="tenant-a",
            action="tenant.first",
            resource_type="tenant",
            resource_id="tenant-a-first",
            result="success",
            payload={"field": "original"},
        )
        await AuditService(uow.session).record(
            tenant_id="tenant-a",
            action="tenant.second",
            resource_type="tenant",
            resource_id="tenant-a-second",
            result="success",
        )

    async with session_factory() as session:
        verification = await AuditService(session).verify_hash_chain("tenant-a")

    assert verification.valid is True
    assert verification.checked == 2
    assert verification.errors == ()

    async with session_factory() as session:
        audit_log = await session.scalar(
            select(AuditLog).where(AuditLog.resource_id == "tenant-a-first")
        )
        assert audit_log is not None
        audit_log.payload = {"field": "tampered"}
        await session.commit()

    async with session_factory() as session:
        verification = await AuditService(session).verify_hash_chain("tenant-a")

    assert verification.valid is False
    assert verification.checked == 2
    assert any(error.startswith("hash_mismatch:") for error in verification.errors)


@pytest.mark.asyncio
async def test_audit_record_rejects_invalid_required_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as exc_info:
            await AuditService(uow.session).record(
                action="",
                resource_type="tenant",
                result="success",
            )

    assert exc_info.value.code == "VALIDATION_ERROR"


async def _audit_logs(session_factory: async_sessionmaker[AsyncSession]) -> list[AuditLog]:
    async with session_factory() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.resource_id))
        audit_logs = list(result.scalars().all())
        for audit_log in audit_logs:
            session.expunge(audit_log)
        return audit_logs


async def _audit_count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        result = await session.scalar(select(func.count()).select_from(AuditLog))
        return int(result or 0)
