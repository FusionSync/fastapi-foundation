from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps import AppRegistry
from core.cli.common import installed_apps, print_payload
from core.config import get_settings
from core.db import unit_of_work
from core.locks import MemoryLockProvider
from core.operations import (
    check_backup_readiness,
    check_config,
    check_process_health,
    run_deployment_smoke,
)
from core.operations.backup import parse_backup_time
from core.outbox import run_outbox_dispatch_loop
from core.scheduler import (
    LockedScheduleProvider,
    ManualScheduleProvider,
    ScheduleRegistry,
    ScheduleTriggerRepository,
    ScheduleTriggerRequest,
)
from core.tasks import SyncTaskProvider, TaskRegistry, TaskRunRepository

_PROFILES = ["local", "private", "cloud"]


def register_operation_commands(subparsers: argparse._SubParsersAction) -> None:
    check_config_parser = subparsers.add_parser("check-config")
    check_config_parser.add_argument("--profile", choices=_PROFILES, required=True)
    check_config_parser.add_argument("--json", action="store_true", dest="as_json")
    check_config_parser.set_defaults(handler=_handle_check_config)

    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--profile", choices=_PROFILES, required=True)
    smoke_parser.add_argument("--json", action="store_true", dest="as_json")
    smoke_parser.set_defaults(handler=_handle_smoke)

    backup_parser = subparsers.add_parser("backup-check")
    backup_parser.add_argument("--profile", choices=_PROFILES, required=True)
    backup_parser.add_argument("--latest-backup-at")
    backup_parser.add_argument("--max-age-hours", type=int)
    backup_parser.add_argument("--json", action="store_true", dest="as_json")
    backup_parser.set_defaults(handler=_handle_backup_check)

    for role in ("serve", "worker", "scheduler", "outbox-dispatcher"):
        role_parser = subparsers.add_parser(role)
        role_parser.add_argument("--json", action="store_true", dest="as_json")
        if role == "worker":
            role_parser.add_argument("--run-once", action="store_true")
            role_parser.add_argument("--database-url")
            role_parser.add_argument("--installed-app", action="append", default=[])
            role_parser.add_argument("--queue", default="default")
            role_parser.add_argument("--tenant-status", default="active")
        if role == "scheduler":
            role_parser.add_argument("--run-once", action="store_true")
            role_parser.add_argument("--database-url")
            role_parser.add_argument("--installed-app", action="append", default=[])
            role_parser.add_argument("--schedule-id")
            role_parser.add_argument("--tenant-id")
            role_parser.add_argument("--request-id", default="scheduler-run-once")
            role_parser.add_argument("--planned-at")
            role_parser.add_argument("--payload-json", default="{}")
            role_parser.add_argument("--tenant-status", default="active")
            role_parser.add_argument("--lock-ttl-seconds", type=int, default=60)
        if role == "outbox-dispatcher":
            role_parser.add_argument("--run", action="store_true")
            role_parser.add_argument("--database-url")
            role_parser.add_argument("--installed-app", action="append", default=[])
            role_parser.add_argument("--dispatcher-id", default="outbox-dispatcher")
            role_parser.add_argument("--batch-size", type=int, default=20)
            role_parser.add_argument("--max-iterations", type=int)
            role_parser.add_argument("--idle-sleep-seconds", type=float, default=1.0)
        role_parser.set_defaults(handler=_handle_process_role, role=role)


def _handle_check_config(args: argparse.Namespace) -> int:
    result = check_config(args.profile)
    print_payload(result.to_dict(), as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_smoke(args: argparse.Namespace) -> int:
    result = run_deployment_smoke(args.profile)
    print_payload(result.to_dict(), as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_backup_check(args: argparse.Namespace) -> int:
    result = check_backup_readiness(
        profile=args.profile,
        latest_backup_at=parse_backup_time(args.latest_backup_at),
        max_age_hours=args.max_age_hours,
    )
    print_payload(result.to_dict(), as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_process_role(args: argparse.Namespace) -> int:
    role = "server" if args.role == "serve" else args.role
    if role == "worker" and args.run_once:
        return _handle_worker_run_once(args)
    if role == "scheduler" and args.run_once:
        return _handle_scheduler_run_once(args)
    if role == "outbox-dispatcher" and args.run:
        return _handle_outbox_dispatcher_run(args)
    result = check_process_health(role)
    payload = {
        **result.to_dict(),
        "command": args.role,
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_worker_run_once(args: argparse.Namespace) -> int:
    try:
        payload = asyncio.run(
            _run_worker_once(
                database_url=_database_url(args.database_url),
                app_modules=installed_apps(args.installed_app),
                queue=args.queue,
                tenant_status=args.tenant_status,
            )
        )
    except Exception as exc:
        print_payload(
            {
                "ok": False,
                "command": args.role,
                "role": "worker",
                "error": f"{type(exc).__name__}: {exc}",
            },
            as_json=args.as_json,
        )
        return 1
    payload = {
        **payload,
        "command": args.role,
        "role": "worker",
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if bool(payload.get("ok")) else 1


def _handle_scheduler_run_once(args: argparse.Namespace) -> int:
    if not args.schedule_id or not args.tenant_id:
        print_payload(
            {
                "ok": False,
                "command": args.role,
                "role": "scheduler",
                "error": "scheduler --run-once requires --schedule-id and --tenant-id",
            },
            as_json=args.as_json,
        )
        return 1
    try:
        payload = asyncio.run(
            _run_scheduler_once(
                database_url=_database_url(args.database_url),
                app_modules=installed_apps(args.installed_app),
                schedule_id=args.schedule_id,
                tenant_id=args.tenant_id,
                request_id=args.request_id,
                planned_at=_parse_datetime(args.planned_at),
                payload=_parse_payload_json(args.payload_json),
                tenant_status=args.tenant_status,
                lock_ttl_seconds=args.lock_ttl_seconds,
            )
        )
    except Exception as exc:
        print_payload(
            {
                "ok": False,
                "command": args.role,
                "role": "scheduler",
                "error": f"{type(exc).__name__}: {exc}",
            },
            as_json=args.as_json,
        )
        return 1
    payload = {
        **payload,
        "command": args.role,
        "role": "scheduler",
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if bool(payload.get("ok")) else 1


def _handle_outbox_dispatcher_run(args: argparse.Namespace) -> int:
    try:
        result = asyncio.run(
            run_outbox_dispatch_loop(
                database_url=_database_url(args.database_url),
                module_paths=installed_apps(args.installed_app),
                dispatcher_id=args.dispatcher_id,
                batch_size=args.batch_size,
                max_iterations=args.max_iterations,
                idle_sleep_seconds=args.idle_sleep_seconds,
            )
        )
    except Exception as exc:
        print_payload(
            {
                "ok": False,
                "command": args.role,
                "role": "outbox-dispatcher",
                "error": f"{type(exc).__name__}: {exc}",
            },
            as_json=args.as_json,
        )
        return 1
    payload = {
        **result.to_dict(),
        "command": args.role,
        "role": "outbox-dispatcher",
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1


async def _run_worker_once(
    *,
    database_url: str,
    app_modules: list[str],
    queue: str,
    tenant_status: str,
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
            task_run = await repository.claim_next_pending(queue=queue)
            if task_run is None:
                return {"ok": True, "claimed": 0, "queue": queue, "task_result": None}
            result = await SyncTaskProvider(
                task_registry,
                task_repository=repository,
            ).run_task_run(
                task_run,
                tenant_status=tenant_status,  # type: ignore[arg-type]
            )
            return {
                "ok": result.ok,
                "claimed": 1,
                "queue": queue,
                "task_result": result.to_dict(),
            }
    finally:
        await engine.dispose()


async def _run_scheduler_once(
    *,
    database_url: str,
    app_modules: list[str],
    schedule_id: str,
    tenant_id: str,
    request_id: str,
    planned_at: datetime | None,
    payload: dict[str, object],
    tenant_status: str,
    lock_ttl_seconds: int,
) -> dict[str, object]:
    app_registry = AppRegistry(app_modules).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        app_registry,
        task_registry=task_registry,
    )
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            if uow.session is None:
                return {"ok": False, "error": "database session was not initialized"}
            provider = LockedScheduleProvider(
                provider=ManualScheduleProvider(
                    schedule_registry=schedule_registry,
                    task_provider=SyncTaskProvider(
                        task_registry,
                        task_repository=TaskRunRepository(uow.session),
                    ),
                    trigger_repository=ScheduleTriggerRepository(uow.session),
                ),
                lock_provider=MemoryLockProvider(),
                lock_ttl_seconds=lock_ttl_seconds,
            )
            result = await provider.trigger(
                ScheduleTriggerRequest(
                    schedule_id=schedule_id,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    planned_at=planned_at,
                    payload=dict(payload),
                ),
                tenant_status=tenant_status,  # type: ignore[arg-type]
            )
            return result.to_dict()
    finally:
        await engine.dispose()


def _database_url(value: str | None) -> str:
    return value or get_settings().database.url


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_payload_json(value: str) -> dict[str, object]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("payload-json must decode to an object")
    return dict(payload)
