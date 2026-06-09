import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.base.models import Model
from core.cli.main import main
from core.idempotency import IdempotencyRecord, hash_request_payload


def test_idempotency_expire_requires_yes(tmp_path: Path, capsys) -> None:
    database_url = _sqlite_url(tmp_path)
    asyncio.run(_seed_idempotency_record(database_url, status="processing", expired=True))

    exit_code = main(
        [
            "idempotency",
            "expire",
            "--database-url",
            database_url,
            "--now",
            "2026-05-28T10:00:00+00:00",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {
        "ok": False,
        "command": "idempotency expire",
        "exit_code": 1,
        "error": {
            "code": "CLI_CONFIRMATION_REQUIRED",
            "message": "idempotency expire requires --yes",
            "details": {},
        },
    }
    assert asyncio.run(_record_status(database_url)) == "processing"


def test_idempotency_expire_marks_expired_records(tmp_path: Path, capsys) -> None:
    database_url = _sqlite_url(tmp_path)
    asyncio.run(_seed_idempotency_record(database_url, status="processing", expired=True))

    exit_code = main(
        [
            "idempotency",
            "expire",
            "--database-url",
            database_url,
            "--now",
            "2026-05-28T10:00:00+00:00",
            "--yes",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "ok": True,
        "command": "idempotency expire",
        "expired": 1,
        "now": "2026-05-28T10:00:00+00:00",
    }
    assert asyncio.run(_record_status(database_url)) == "expired"


def test_idempotency_diagnose_reports_replayable_response(tmp_path: Path, capsys) -> None:
    database_url = _sqlite_url(tmp_path)
    request_hash = hash_request_payload({"file_name": "proposal.docx"})
    asyncio.run(
        _seed_idempotency_record(
            database_url,
            status="succeeded",
            expired=False,
            request_hash=request_hash,
            response_code="CREATED",
            response_body={"id": "file-1"},
            outbox_event_id="evt-1",
        )
    )

    exit_code = main(
        [
            "idempotency",
            "diagnose",
            "--database-url",
            database_url,
            "--tenant-id",
            "tenant-a",
            "--user-id",
            "user-1",
            "--route",
            "POST /files",
            "--idempotency-key",
            "idem-1",
            "--request-hash",
            request_hash,
            "--now",
            "2026-05-28T10:00:00+00:00",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["diagnosis"] == "replayable"
    assert payload["record"]["status"] == "succeeded"
    assert payload["replay"] == {
        "response_code": "CREATED",
        "response_body": {"id": "file-1"},
        "task_id": None,
        "outbox_event_id": "evt-1",
    }


def test_idempotency_diagnose_reports_request_hash_conflict(tmp_path: Path, capsys) -> None:
    database_url = _sqlite_url(tmp_path)
    original_hash = hash_request_payload({"file_name": "proposal.docx"})
    requested_hash = hash_request_payload({"file_name": "other.docx"})
    asyncio.run(
        _seed_idempotency_record(
            database_url,
            status="succeeded",
            expired=False,
            request_hash=original_hash,
        )
    )

    exit_code = main(
        [
            "idempotency",
            "diagnose",
            "--database-url",
            database_url,
            "--tenant-id",
            "tenant-a",
            "--user-id",
            "user-1",
            "--route",
            "POST /files",
            "--idempotency-key",
            "idem-1",
            "--request-hash",
            requested_hash,
            "--now",
            "2026-05-28T10:00:00+00:00",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["diagnosis"] == "request_hash_conflict"
    assert payload["record"]["request_hash"] == original_hash
    assert payload["requested_request_hash"] == requested_hash


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'idempotency.db'}"


async def _seed_idempotency_record(
    database_url: str,
    *,
    status: str,
    expired: bool,
    request_hash: str | None = None,
    response_code: str | None = None,
    response_body: dict[str, object] | None = None,
    outbox_event_id: str | None = None,
) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Model.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
    resolved_request_hash = request_hash or hash_request_payload({"file_name": "proposal.docx"})
    try:
        async with session_factory() as session:
            session.add(
                IdempotencyRecord(
                    tenant_id="tenant-a",
                    user_id="user-1",
                    route="POST /files",
                    idempotency_key="idem-1",
                    request_hash=resolved_request_hash,
                    status=status,
                    response_code=response_code,
                    response_body=response_body,
                    outbox_event_id=outbox_event_id,
                    locked_until=now + timedelta(seconds=60),
                    expires_at=now - timedelta(seconds=1) if expired else now + timedelta(hours=1),
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _record_status(database_url: str) -> str:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            record = await session.get(IdempotencyRecord, (await _record_id(session)))
            assert record is not None
            return record.status
    finally:
        await engine.dispose()


async def _record_id(session) -> str:
    from sqlalchemy import select

    record_id = await session.scalar(select(IdempotencyRecord.id).limit(1))
    assert record_id is not None
    return record_id
