from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps import AppRegistry
from core.cli.common import installed_apps, print_payload
from core.config import get_settings
from core.db import unit_of_work
from core.exceptions import AppError
from core.tasks import SyncTaskProvider, TaskRegistry, TaskRun, TaskRunRepository


def register_task_commands(subparsers: argparse._SubParsersAction) -> None:
    tasks_parser = subparsers.add_parser("tasks")
    tasks_subparsers = tasks_parser.add_subparsers(dest="tasks_command", required=True)

    failed_parser = tasks_subparsers.add_parser("failed")
    failed_subparsers = failed_parser.add_subparsers(dest="failed_command", required=True)

    list_parser = failed_subparsers.add_parser("list")
    list_parser.add_argument("--database-url")
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--json", action="store_true", dest="as_json")
    list_parser.set_defaults(handler=_handle_failed_list)

    retry_parser = failed_subparsers.add_parser("retry")
    retry_parser.add_argument("--database-url")
    retry_parser.add_argument("--installed-app", action="append", default=[])
    retry_parser.add_argument("--task-id", required=True)
    retry_parser.add_argument("--yes", action="store_true")
    retry_parser.add_argument("--json", action="store_true", dest="as_json")
    retry_parser.set_defaults(handler=_handle_failed_retry)

    running_parser = tasks_subparsers.add_parser("running")
    running_subparsers = running_parser.add_subparsers(
        dest="running_command",
        required=True,
    )

    recover_parser = running_subparsers.add_parser("recover")
    recover_parser.add_argument("--database-url")
    recover_parser.add_argument("--older-than-seconds", type=int, required=True)
    recover_parser.add_argument("--limit", type=int, default=50)
    recover_parser.add_argument("--yes", action="store_true")
    recover_parser.add_argument("--json", action="store_true", dest="as_json")
    recover_parser.set_defaults(handler=_handle_running_recover)


def _handle_failed_list(args: argparse.Namespace) -> int:
    payload = asyncio.run(
        _list_failed_tasks(
            database_url=_database_url(args.database_url),
            limit=args.limit,
        )
    )
    print_payload(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


def _handle_failed_retry(args: argparse.Namespace) -> int:
    if not args.yes:
        print_payload(
            {"ok": False, "error": "tasks failed retry requires --yes"},
            as_json=args.as_json,
        )
        return 1
    payload = asyncio.run(
        _retry_failed_task(
            database_url=_database_url(args.database_url),
            task_id=args.task_id,
            app_modules=installed_apps(args.installed_app),
        )
    )
    print_payload(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


def _handle_running_recover(args: argparse.Namespace) -> int:
    if not args.yes:
        print_payload(
            {"ok": False, "error": "tasks running recover requires --yes"},
            as_json=args.as_json,
        )
        return 1
    payload = asyncio.run(
        _recover_running_tasks(
            database_url=_database_url(args.database_url),
            older_than_seconds=args.older_than_seconds,
            limit=args.limit,
        )
    )
    print_payload(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


async def _list_failed_tasks(*, database_url: str, limit: int) -> dict[str, object]:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            tasks = await TaskRunRepository(session).list_failed(limit=limit)
            return {
                "ok": True,
                "count": len(tasks),
                "tasks": [_task_run_to_dict(task_run) for task_run in tasks],
            }
    finally:
        await engine.dispose()


async def _recover_running_tasks(
    *,
    database_url: str,
    older_than_seconds: int,
    limit: int,
) -> dict[str, object]:
    if older_than_seconds <= 0:
        return {"ok": False, "error": "older-than-seconds must be positive"}
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    older_than = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    try:
        async with unit_of_work(session_factory) as uow:
            if uow.session is None:
                return {"ok": False, "error": "database session was not initialized"}
            tasks = await TaskRunRepository(uow.session).recover_stale_running(
                older_than=older_than,
                limit=limit,
            )
            return {
                "ok": True,
                "count": len(tasks),
                "tasks": [_task_run_to_dict(task_run) for task_run in tasks],
            }
    finally:
        await engine.dispose()


async def _retry_failed_task(
    *,
    database_url: str,
    task_id: str,
    app_modules: list[str],
) -> dict[str, object]:
    app_registry = AppRegistry(app_modules).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            if uow.session is None:
                return {"ok": False, "error": "database session was not initialized"}
            repository = TaskRunRepository(uow.session)
            try:
                task_run = await repository.require(task_id)
                result = await SyncTaskProvider(
                    task_registry,
                    task_repository=repository,
                ).retry(task_run)
            except AppError as exc:
                return {"ok": False, "code": exc.code, "error": exc.message}
            return {
                "ok": result.ok,
                "task": _task_run_to_dict(task_run),
                "result": result.to_dict(),
            }
    finally:
        await engine.dispose()


def _database_url(value: str | None) -> str:
    return value or get_settings().database.url


def _task_run_to_dict(task_run: TaskRun) -> dict[str, object]:
    return {
        "task_id": task_run.id,
        "tenant_id": task_run.tenant_id,
        "task_type": task_run.task_type,
        "idempotency_key": task_run.idempotency_key,
        "status": task_run.status,
        "queue": task_run.queue,
        "attempt_count": task_run.attempt_count,
        "max_attempts": task_run.max_attempts,
        "request_id": task_run.request_id,
        "error_message": task_run.error_message,
        "result_payload": task_run.result_payload,
    }
