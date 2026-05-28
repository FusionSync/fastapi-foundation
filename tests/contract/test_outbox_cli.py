import asyncio
import json
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.cli.main import main
from core.outbox import OutboxEvent


def test_outbox_dead_letter_list_outputs_stable_json(tmp_path: Path, capsys) -> None:
    database_url = _sqlite_url(tmp_path)
    event_id = asyncio.run(_seed_dead_letter(database_url))

    exit_code = main(
        [
            "outbox",
            "dead-letter",
            "list",
            "--database-url",
            database_url,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["events"][0]["id"] == event_id
    assert payload["events"][0]["status"] == "dead_letter"
    assert payload["events"][0]["dead_letter_reason"] == "permanent failure"


def test_outbox_dead_letter_replay_requires_yes(tmp_path: Path, capsys) -> None:
    database_url = _sqlite_url(tmp_path)
    event_id = asyncio.run(_seed_dead_letter(database_url))

    exit_code = main(
        [
            "outbox",
            "dead-letter",
            "replay",
            "--event-id",
            event_id,
            "--database-url",
            database_url,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {
        "ok": False,
        "error": "outbox dead-letter replay requires --yes",
    }
    assert asyncio.run(_event_status(database_url, event_id)) == "dead_letter"


def test_outbox_dead_letter_replay_moves_event_to_pending(tmp_path: Path, capsys) -> None:
    database_url = _sqlite_url(tmp_path)
    event_id = asyncio.run(_seed_dead_letter(database_url))

    exit_code = main(
        [
            "outbox",
            "dead-letter",
            "replay",
            "--event-id",
            event_id,
            "--database-url",
            database_url,
            "--yes",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "ok": True,
        "event_id": event_id,
        "status": "pending",
    }
    assert asyncio.run(_event_status(database_url, event_id)) == "pending"


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'outbox-cli.db'}"


async def _seed_dead_letter(database_url: str) -> str:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            event = OutboxEvent(
                tenant_id="tenant-a",
                event_type="business.created",
                event_version=1,
                aggregate_type="business_record",
                aggregate_id="record-1",
                payload={
                    "tenant_id": "tenant-a",
                    "actor_id": "user-1",
                    "request_id": "req_test",
                },
                status="dead_letter",
                attempt_count=1,
                max_attempts=1,
                last_error="RuntimeError: permanent failure",
                dead_letter_reason="permanent failure",
            )
            session.add(event)
            await session.commit()
            return event.id
    finally:
        await engine.dispose()


async def _event_status(database_url: str, event_id: str) -> str:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            event = await session.get(OutboxEvent, event_id)
            assert event is not None
            return event.status
    finally:
        await engine.dispose()
