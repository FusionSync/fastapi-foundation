from __future__ import annotations

import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps import AppRegistry
from core.cli.common import installed_apps, print_payload
from core.config import get_settings
from core.db import unit_of_work
from core.events import EventRegistry
from core.outbox import (
    OutboxDispatcher,
    OutboxRepository,
    list_dead_letter_events,
    replay_dead_letter_by_id,
)


def register_outbox_commands(subparsers: argparse._SubParsersAction) -> None:
    outbox_parser = subparsers.add_parser("outbox")
    outbox_subparsers = outbox_parser.add_subparsers(dest="outbox_command", required=True)

    dispatch_parser = outbox_subparsers.add_parser("dispatch-once")
    dispatch_parser.add_argument("--database-url")
    dispatch_parser.add_argument("--installed-app", action="append", default=[])
    dispatch_parser.add_argument("--dispatcher-id", default="outbox-dispatcher")
    dispatch_parser.add_argument("--batch-size", type=int, default=20)
    dispatch_parser.add_argument("--json", action="store_true", dest="as_json")
    dispatch_parser.set_defaults(handler=_handle_dispatch_once)

    dead_letter_parser = outbox_subparsers.add_parser("dead-letter")
    dead_letter_subparsers = dead_letter_parser.add_subparsers(
        dest="dead_letter_command",
        required=True,
    )

    list_parser = dead_letter_subparsers.add_parser("list")
    list_parser.add_argument("--database-url")
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--json", action="store_true", dest="as_json")
    list_parser.set_defaults(handler=_handle_dead_letter_list)

    replay_parser = dead_letter_subparsers.add_parser("replay")
    replay_parser.add_argument("--database-url")
    replay_parser.add_argument("--event-id", required=True)
    replay_parser.add_argument("--yes", action="store_true")
    replay_parser.add_argument("--json", action="store_true", dest="as_json")
    replay_parser.set_defaults(handler=_handle_dead_letter_replay)


def _handle_dispatch_once(args: argparse.Namespace) -> int:
    try:
        payload = asyncio.run(
            _dispatch_once(
                database_url=_database_url(args.database_url),
                module_paths=installed_apps(args.installed_app),
                dispatcher_id=args.dispatcher_id,
                batch_size=args.batch_size,
            )
        )
    except Exception as exc:
        print_payload(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            },
            as_json=args.as_json,
        )
        return 1
    print_payload(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


def _handle_dead_letter_list(args: argparse.Namespace) -> int:
    payload = asyncio.run(
        _list_dead_letters(
            database_url=_database_url(args.database_url),
            limit=args.limit,
        )
    )
    print_payload(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


def _handle_dead_letter_replay(args: argparse.Namespace) -> int:
    if not args.yes:
        print_payload(
            {
                "ok": False,
                "error": "outbox dead-letter replay requires --yes",
            },
            as_json=args.as_json,
        )
        return 1
    payload = asyncio.run(
        _replay_dead_letter(
            database_url=_database_url(args.database_url),
            event_id=args.event_id,
        )
    )
    print_payload(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


async def _dispatch_once(
    *,
    database_url: str,
    module_paths: list[str],
    dispatcher_id: str,
    batch_size: int,
) -> dict[str, object]:
    registry = EventRegistry.from_app_registry(AppRegistry(module_paths).load())
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            if uow.session is None:
                return {"ok": False, "error": "database session was not initialized"}
            stats = await OutboxDispatcher(
                OutboxRepository(uow.session, registry=registry),
                registry,
                dispatcher_id=dispatcher_id,
                batch_size=batch_size,
            ).dispatch_once()
            ok = stats.failed == 0 and stats.dead_lettered == 0
            return {
                "ok": ok,
                "dispatcher_id": dispatcher_id,
                "claimed": stats.claimed,
                "published": stats.published,
                "failed": stats.failed,
                "dead_lettered": stats.dead_lettered,
            }
    finally:
        await engine.dispose()


async def _list_dead_letters(*, database_url: str, limit: int) -> dict[str, object]:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            events = await list_dead_letter_events(session, limit=limit)
            return {"ok": True, "count": len(events), "events": events}
    finally:
        await engine.dispose()


async def _replay_dead_letter(*, database_url: str, event_id: str) -> dict[str, object]:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            if uow.session is None:
                return {"ok": False, "error": "database session was not initialized"}
            result = await replay_dead_letter_by_id(uow.session, event_id=event_id)
            return result.to_dict()
    finally:
        await engine.dispose()


def _database_url(value: str | None) -> str:
    return value or get_settings().database.url
