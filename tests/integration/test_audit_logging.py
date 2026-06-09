import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import Model
from core.context import RequestContext, reset_current_context, set_current_context
from core.db import unit_of_work
from core.exceptions import AppError
from core.locks import MemoryLockProvider
from platform_apps.audit import (
    AuditExportRecord,
    AuditExportService,
    AuditLog,
    AuditService,
    LocalWormAuditExportSink,
    audit_hash,
)


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
async def test_audit_record_uses_context_redacts_payload_and_sets_hash_chain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token = set_current_context(
        RequestContext(
            request_id="req-1",
            trace_id="trace-1",
            user_id="user-1",
            tenant_id="tenant-a",
            ip_address="127.0.0.1",
            user_agent="pytest",
            route="/api/v1/workspaces/{workspace_id}",
            method="POST",
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
    assert audit_logs[0].trace_id == "trace-1"
    assert audit_logs[0].route == "/api/v1/workspaces/{workspace_id}"
    assert audit_logs[0].method == "POST"
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
async def test_audit_record_serializes_writes_until_transaction_ends(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    second_started = asyncio.Event()

    async with unit_of_work(session_factory) as first_uow:
        assert first_uow.session is not None
        first = await AuditService(first_uow.session).record(
            tenant_id="tenant-a",
            action="tenant.first",
            resource_type="tenant",
            resource_id="tenant-first",
            result="success",
        )

        async def write_second() -> AuditLog:
            async with unit_of_work(session_factory) as second_uow:
                assert second_uow.session is not None
                second_started.set()
                return await AuditService(second_uow.session).record(
                    tenant_id="tenant-a",
                    action="tenant.second",
                    resource_type="tenant",
                    resource_id="tenant-second",
                    result="success",
                )

        second_task = asyncio.create_task(write_second())
        await second_started.wait()
        await asyncio.sleep(0)

        assert second_task.done() is False

    second = await second_task
    audit_logs = {log.resource_id: log for log in await _audit_logs(session_factory)}

    assert audit_logs["tenant-first"].hash == first.hash
    assert audit_logs["tenant-second"].hash == second.hash
    assert audit_logs["tenant-second"].hash_prev == first.hash


@pytest.mark.asyncio
async def test_audit_record_uses_distributed_chain_lock_until_transaction_ends(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    locks = MemoryLockProvider()

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await AuditService(
            uow.session,
            lock_provider=locks,
            lock_owner_token="audit-worker-1",
            lock_ttl_seconds=60,
        ).record(
            tenant_id="tenant-a",
            action="tenant.first",
            resource_type="tenant",
            resource_id="tenant-first",
            result="success",
        )

        assert await locks.locked("audit:hash-chain:tenant:tenant-a") is True

    await asyncio.sleep(0)
    assert await locks.locked("audit:hash-chain:tenant:tenant-a") is False


@pytest.mark.asyncio
async def test_audit_record_rejects_when_distributed_chain_lock_is_held(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    locks = MemoryLockProvider()
    await locks.acquire(
        "audit:hash-chain:tenant:tenant-a",
        owner_token="audit-worker-2",
        ttl_seconds=60,
    )

    with pytest.raises(AppError) as exc_info:
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            await AuditService(
                uow.session,
                lock_provider=locks,
                lock_owner_token="audit-worker-1",
                lock_ttl_seconds=60,
            ).record(
                tenant_id="tenant-a",
                action="tenant.first",
                resource_type="tenant",
                resource_id="tenant-first",
                result="success",
            )

    assert exc_info.value.code == "LOCK_NOT_ACQUIRED"
    assert await _audit_count(session_factory) == 0
    assert await locks.locked("audit:hash-chain:tenant:tenant-a") is True


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
async def test_audit_export_writes_worm_jsonl_manifest_and_records_export(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        first = await AuditService(uow.session).record(
            tenant_id="tenant-a",
            actor_id="user-1",
            action="tenant.first",
            resource_type="tenant",
            resource_id="tenant-a-first",
            result="success",
            payload={"password": "secret", "visible": "yes"},
        )
        second = await AuditService(uow.session).record(
            tenant_id="tenant-a",
            actor_id="user-2",
            action="tenant.second",
            resource_type="tenant",
            resource_id="tenant-a-second",
            result="failure",
            reason="denied by policy",
            policy_version=9,
            request_id="req-2",
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        export_record = await AuditExportService(uow.session).export_logs(
            tenant_id="tenant-a",
            destination_type="worm",
            sink=LocalWormAuditExportSink(tmp_path),
            export_id="export-tenant-a",
            actor_id="auditor-1",
            request_id="req-export-1",
        )

    export_path = tmp_path / "export-tenant-a.jsonl"
    assert export_record.id == "export-tenant-a"
    assert export_record.status == "succeeded"
    assert export_record.destination_type == "worm"
    assert export_record.destination_uri == str(export_path)
    assert export_record.actor_id == "auditor-1"
    assert export_record.filters == {"tenant_id": "tenant-a"}
    assert export_record.record_count == 2
    assert export_record.hash_root == first.hash
    assert export_record.hash_tip == second.hash
    assert export_record.checksum_sha256 == hashlib.sha256(export_path.read_bytes()).hexdigest()
    assert export_record.request_id == "req-export-1"
    assert export_record.exported_at is not None

    payload_lines = [json.loads(line) for line in export_path.read_text().splitlines()]
    assert payload_lines[0] == {
        "type": "audit_export_manifest",
        "format": "audit.ndjson.v1",
        "export_id": "export-tenant-a",
        "destination_type": "worm",
        "tenant_id": "tenant-a",
        "filters": {"tenant_id": "tenant-a"},
        "record_count": 2,
        "hash_root": first.hash,
        "hash_tip": second.hash,
    }
    assert [line["resource_id"] for line in payload_lines[1:]] == [
        "tenant-a-first",
        "tenant-a-second",
    ]
    assert payload_lines[1]["type"] == "audit_log"
    assert payload_lines[1]["hash"] == first.hash
    assert payload_lines[1]["hash_prev"] is None
    assert payload_lines[1]["payload"] == {"password": "***REDACTED***", "visible": "yes"}
    assert payload_lines[2]["type"] == "audit_log"
    assert payload_lines[2]["hash"] == second.hash
    assert payload_lines[2]["hash_prev"] == first.hash

    exports = await _audit_exports(session_factory)
    assert len(exports) == 1
    assert exports[0].id == "export-tenant-a"
    assert exports[0].status == "succeeded"
    assert exports[0].destination_uri == str(export_path)


@pytest.mark.asyncio
async def test_audit_export_rejects_tampered_chain_before_writing(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
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
        audit_log = await session.scalar(
            select(AuditLog).where(AuditLog.resource_id == "tenant-a-first")
        )
        assert audit_log is not None
        audit_log.payload = {"field": "tampered"}
        await session.commit()

    with pytest.raises(AppError) as exc_info:
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            await AuditExportService(uow.session).export_logs(
                tenant_id="tenant-a",
                destination_type="worm",
                sink=LocalWormAuditExportSink(tmp_path),
                export_id="tampered-export",
            )

    assert exc_info.value.code == "CONFLICT"
    assert "hash chain is invalid" in str(exc_info.value)
    assert (tmp_path / "tampered-export.jsonl").exists() is False
    assert await _audit_export_count(session_factory) == 0


@pytest.mark.asyncio
async def test_worm_export_sink_rejects_overwriting_existing_export(tmp_path: Path) -> None:
    sink = LocalWormAuditExportSink(tmp_path)

    result = await sink.write(export_id="same-export", payload=b"first\n")

    assert result.destination_uri == str(tmp_path / "same-export.jsonl")
    assert (tmp_path / "same-export.jsonl").read_bytes() == b"first\n"
    with pytest.raises(AppError) as exc_info:
        await sink.write(export_id="same-export", payload=b"second\n")

    assert exc_info.value.code == "CONFLICT"
    assert (tmp_path / "same-export.jsonl").read_bytes() == b"first\n"


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


async def _audit_exports(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[AuditExportRecord]:
    async with session_factory() as session:
        result = await session.execute(select(AuditExportRecord).order_by(AuditExportRecord.id))
        audit_exports = list(result.scalars().all())
        for audit_export in audit_exports:
            session.expunge(audit_export)
        return audit_exports


async def _audit_export_count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        result = await session.scalar(select(func.count()).select_from(AuditExportRecord))
        return int(result or 0)
