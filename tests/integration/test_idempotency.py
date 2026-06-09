from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import Model
from core.db import unit_of_work
from core.exceptions import AppError
from core.idempotency import IdempotencyRecord, IdempotencyStore, hash_request_payload


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
async def test_first_claim_creates_processing_record(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 5, 28, tzinfo=UTC)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        claim = await IdempotencyStore(uow.session).claim(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /files",
            idempotency_key="idem-1",
            request_hash=hash_request_payload({"file_name": "proposal.docx"}),
            now=now,
            ttl_seconds=3600,
            lock_seconds=30,
        )

    assert claim.outcome == "started"
    assert claim.record.tenant_id == "tenant-a"
    assert claim.record.user_id == "user-1"
    assert claim.record.route == "POST /files"
    assert claim.record.idempotency_key == "idem-1"
    assert claim.record.status == "processing"
    assert claim.record.locked_until == now + timedelta(seconds=30)
    assert claim.record.expires_at == now + timedelta(seconds=3600)
    assert await _record_count(session_factory) == 1


@pytest.mark.asyncio
async def test_same_processing_key_returns_in_progress(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    request_hash = hash_request_payload({"file_name": "proposal.docx"})

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await IdempotencyStore(uow.session).claim(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /files",
            idempotency_key="idem-1",
            request_hash=request_hash,
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as in_progress:
            await IdempotencyStore(uow.session).claim(
                tenant_id="tenant-a",
                user_id="user-1",
                route="POST /files",
                idempotency_key="idem-1",
                request_hash=request_hash,
            )

    assert in_progress.value.code == "IDEMPOTENCY_IN_PROGRESS"
    assert in_progress.value.details is not None
    assert in_progress.value.details["retry_after"] >= 0
    assert await _record_count(session_factory) == 1


@pytest.mark.asyncio
async def test_same_key_with_different_request_hash_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await IdempotencyStore(uow.session).claim(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /files",
            idempotency_key="idem-1",
            request_hash=hash_request_payload({"file_name": "proposal.docx"}),
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as conflict:
            await IdempotencyStore(uow.session).claim(
                tenant_id="tenant-a",
                user_id="user-1",
                route="POST /files",
                idempotency_key="idem-1",
                request_hash=hash_request_payload({"file_name": "other.docx"}),
            )

    assert conflict.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert await _record_count(session_factory) == 1


@pytest.mark.asyncio
async def test_succeeded_claim_replays_stored_response(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    request_hash = hash_request_payload({"file_name": "proposal.docx"})

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        store = IdempotencyStore(uow.session)
        claim = await store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /files",
            idempotency_key="idem-1",
            request_hash=request_hash,
        )
        await store.mark_succeeded(
            claim.record,
            response_code="OK",
            response_body={"id": "file-1"},
            outbox_event_id="evt-1",
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        replayed = await IdempotencyStore(uow.session).claim(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /files",
            idempotency_key="idem-1",
            request_hash=request_hash,
        )

    assert replayed.outcome == "replayed"
    assert replayed.record.response_code == "OK"
    assert replayed.record.response_body == {"id": "file-1"}
    assert replayed.record.outbox_event_id == "evt-1"
    assert await _record_count(session_factory) == 1


@pytest.mark.asyncio
async def test_expired_processing_lock_can_be_reclaimed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    request_hash = hash_request_payload({"file_name": "proposal.docx"})
    now = datetime(2026, 5, 28, tzinfo=UTC)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        original = await IdempotencyStore(uow.session).claim(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /files",
            idempotency_key="idem-1",
            request_hash=request_hash,
            now=now,
            lock_seconds=10,
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        reclaimed = await IdempotencyStore(uow.session).claim(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /files",
            idempotency_key="idem-1",
            request_hash=request_hash,
            now=now + timedelta(seconds=11),
            lock_seconds=30,
        )

    assert reclaimed.outcome == "started"
    assert reclaimed.record.id == original.record.id
    assert reclaimed.record.locked_until == now + timedelta(seconds=41)
    assert await _record_count(session_factory) == 1


async def _record_count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        result = await session.scalar(select(func.count()).select_from(IdempotencyRecord))
        return int(result or 0)
