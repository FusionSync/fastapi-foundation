from __future__ import annotations

import argparse
import asyncio
import json
import signal
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.app import create_app
from core.apps import AppRegistry, resolve_runtime_capabilities
from core.cli.common import (
    CLI_RUNTIME_ERROR,
    CLI_USAGE_ERROR,
    error_payload,
    exception_error_payload,
    installed_apps,
    print_payload,
)
from core.config import Settings, get_settings
from core.db import unit_of_work
from core.locks import MemoryLockProvider
from core.operations import (
    check_backup_readiness,
    check_config,
    check_process_health,
    run_deployment_smoke,
    run_release_checkpoint,
)
from core.operations.backup import parse_backup_time
from core.outbox import OutboxDispatchRunResult, run_outbox_dispatch_loop
from core.scheduler import (
    LockedScheduleProvider,
    ManualScheduleProvider,
    ScheduleRegistry,
    ScheduleTriggerRepository,
    ScheduleTriggerRequest,
    run_scheduler_loop,
    wrap_external_scheduler_provider,
)
from core.tasks import (
    DatabaseQueueTaskProvider,
    SyncTaskProvider,
    TaskRegistry,
    TaskRunRepository,
    run_task_worker_loop,
)

_PROFILES = ["local", "private", "cloud"]
_ARTIFACT_TARGETS = ["docker-compose", "systemd", "helm-values"]
_PROCESS_ROLES = ["server", "worker", "scheduler", "outbox-dispatcher", "migrate"]


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

    release_parser = subparsers.add_parser("release")
    release_subparsers = release_parser.add_subparsers(dest="release_command", required=True)
    checkpoint_parser = release_subparsers.add_parser("checkpoint")
    checkpoint_parser.add_argument("--profile", choices=_PROFILES, required=True)
    checkpoint_parser.add_argument("--artifact-target", choices=_ARTIFACT_TARGETS, required=True)
    checkpoint_parser.add_argument("--actual", action="append", default=[])
    checkpoint_parser.add_argument("--role-actual", action="append", default=[])
    checkpoint_parser.add_argument("--latest-backup-at")
    checkpoint_parser.add_argument("--max-age-hours", type=int)
    checkpoint_parser.add_argument("--installed-app", action="append", default=[])
    checkpoint_parser.add_argument("--json", action="store_true", dest="as_json")
    checkpoint_parser.set_defaults(handler=_handle_release_checkpoint)

    for role in ("serve", "worker", "scheduler", "outbox-dispatcher"):
        role_parser = subparsers.add_parser(role)
        role_parser.add_argument("--json", action="store_true", dest="as_json")
        if role == "serve":
            role_parser.add_argument("--run", action="store_true")
            role_parser.add_argument("--host", default="0.0.0.0")
            role_parser.add_argument("--port", type=int, default=8000)
            role_parser.add_argument("--reload", action="store_true")
            role_parser.add_argument("--workers", type=int, default=1)
            role_parser.add_argument("--installed-app", action="append", default=[])
            role_parser.add_argument("--database-url")
            role_parser.add_argument("--dry-run", action="store_true")
        if role == "worker":
            role_parser.add_argument("--run", action="store_true")
            role_parser.add_argument("--run-once", action="store_true")
            role_parser.add_argument("--database-url")
            role_parser.add_argument("--installed-app", action="append", default=[])
            role_parser.add_argument("--queue", default="default")
            role_parser.add_argument("--provider", choices=["sync", "database"])
            role_parser.add_argument("--max-attempts", type=int)
            role_parser.add_argument("--retry-backoff-seconds", type=int)
            role_parser.add_argument("--tenant-status", default="active")
            role_parser.add_argument("--instance-id")
            role_parser.add_argument("--max-iterations", type=int)
            role_parser.add_argument("--idle-sleep-seconds", type=float, default=1.0)
        if role == "scheduler":
            role_parser.add_argument("--run", action="store_true")
            role_parser.add_argument("--run-once", action="store_true")
            role_parser.add_argument("--database-url")
            role_parser.add_argument("--installed-app", action="append", default=[])
            role_parser.add_argument("--schedule-id")
            role_parser.add_argument("--tenant-id")
            role_parser.add_argument("--request-id", default="scheduler-run-once")
            role_parser.add_argument("--request-id-prefix", default="scheduler-run")
            role_parser.add_argument("--planned-at")
            role_parser.add_argument("--now")
            role_parser.add_argument("--payload-json", default="{}")
            role_parser.add_argument("--tenant-status", default="active")
            role_parser.add_argument(
                "--provider",
                choices=["local", "apscheduler", "celery_beat"],
            )
            role_parser.add_argument("--instance-id")
            role_parser.add_argument("--max-iterations", type=int)
            role_parser.add_argument("--idle-sleep-seconds", type=float)
            role_parser.add_argument("--lock-ttl-seconds", type=int)
        if role == "outbox-dispatcher":
            role_parser.add_argument("--run", action="store_true")
            role_parser.add_argument("--database-url")
            role_parser.add_argument("--installed-app", action="append", default=[])
            role_parser.add_argument("--dispatcher-id", default="outbox-dispatcher")
            role_parser.add_argument("--instance-id")
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


def _handle_release_checkpoint(args: argparse.Namespace) -> int:
    try:
        actual_env = _parse_actual_env(args.actual)
        role_actual_env = _parse_role_actual_env(args.role_actual)
    except ValueError as exc:
        print_payload(
            error_payload(
                code=CLI_USAGE_ERROR,
                message=str(exc),
                command="release checkpoint",
                exit_code=2,
            ),
            as_json=args.as_json,
        )
        return 2
    result = run_release_checkpoint(
        profile=args.profile,
        artifact_target=args.artifact_target,
        actual_env=actual_env,
        role_actual_env=role_actual_env,
        latest_backup_at=parse_backup_time(args.latest_backup_at),
        max_backup_age_hours=args.max_age_hours,
        installed_apps=installed_apps(args.installed_app),
    )
    payload = {
        "command": "release checkpoint",
        **result.to_dict(),
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_process_role(args: argparse.Namespace) -> int:
    role = "server" if args.role == "serve" else args.role
    if role == "server" and args.run:
        return _handle_serve_run(args)
    if role == "worker" and args.run_once:
        return _handle_worker_run_once(args)
    if role == "worker" and args.run:
        return _handle_worker_run(args)
    if role == "scheduler" and args.run_once:
        return _handle_scheduler_run_once(args)
    if role == "scheduler" and args.run:
        return _handle_scheduler_run(args)
    if role == "outbox-dispatcher" and args.run:
        return _handle_outbox_dispatcher_run(args)
    result = check_process_health(role)
    payload = {
        **result.to_dict(),
        "command": args.role,
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_serve_run(args: argparse.Namespace) -> int:
    settings = _runtime_settings(
        installed_app_paths=installed_apps(args.installed_app),
        database_url=args.database_url,
        service_role="server",
    )
    try:
        app = create_app(settings)
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command=args.role, role="server"),
            as_json=args.as_json,
        )
        return 1

    health = check_process_health("server", settings=settings)
    payload = {
        "ok": health.ok,
        "command": args.role,
        "role": "server",
        "mode": "dry-run" if args.dry_run else "serve",
        "host": args.host,
        "port": args.port,
        "reload": args.reload,
        "workers": args.workers,
        "installed_apps": settings.installed_apps,
        "checks": health.checks,
        "details": {
            **health.details,
            "route_count": len(app.routes),
            "app_name": settings.app.name,
            "app_version": settings.app.version,
        },
    }
    if args.dry_run:
        print_payload(payload, as_json=args.as_json)
        asyncio.run(app.state.database_engine.dispose())
        return 0 if health.ok else 1

    import uvicorn

    print_payload(payload, as_json=args.as_json)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload, workers=args.workers)
    return 0


def _handle_worker_run_once(args: argparse.Namespace) -> int:
    try:
        payload = asyncio.run(
            _run_worker_once(
                database_url=_database_url(args.database_url),
                app_modules=installed_apps(args.installed_app),
                queue=args.queue,
                provider=_task_queue_provider(args.provider),
                max_attempts=_task_queue_max_attempts(args.max_attempts),
                retry_backoff_seconds=_task_queue_retry_backoff_seconds(
                    args.retry_backoff_seconds
                ),
                tenant_status=args.tenant_status,
            )
        )
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command=args.role, role="worker"),
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


def _handle_worker_run(args: argparse.Namespace) -> int:
    try:
        result = asyncio.run(
            run_task_worker_loop(
                database_url=_database_url(args.database_url),
                module_paths=installed_apps(args.installed_app),
                queue=args.queue,
                tenant_status=args.tenant_status,
                provider=_task_queue_provider(args.provider),
                max_attempts=_task_queue_max_attempts(args.max_attempts),
                retry_backoff_seconds=_task_queue_retry_backoff_seconds(
                    args.retry_backoff_seconds
                ),
                instance_id=args.instance_id,
                max_iterations=args.max_iterations,
                idle_sleep_seconds=args.idle_sleep_seconds,
            )
        )
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command=args.role, role="worker"),
            as_json=args.as_json,
        )
        return 1
    payload = {
        **result.to_dict(),
        "command": args.role,
        "role": "worker",
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_scheduler_run_once(args: argparse.Namespace) -> int:
    if not args.schedule_id or not args.tenant_id:
        print_payload(
            error_payload(
                code=CLI_USAGE_ERROR,
                message="scheduler --run-once requires --schedule-id and --tenant-id",
                command=args.role,
                exit_code=2,
                role="scheduler",
            ),
            as_json=args.as_json,
        )
        return 2
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
                lock_ttl_seconds=_scheduler_lock_ttl_seconds(args.lock_ttl_seconds),
                provider=_scheduler_provider(args.provider),
            )
        )
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command=args.role, role="scheduler"),
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


def _handle_scheduler_run(args: argparse.Namespace) -> int:
    if not args.tenant_id:
        print_payload(
            error_payload(
                code=CLI_USAGE_ERROR,
                message="scheduler --run requires --tenant-id",
                command=args.role,
                exit_code=2,
                role="scheduler",
            ),
            as_json=args.as_json,
        )
        return 2
    try:
        result = asyncio.run(
            run_scheduler_loop(
                database_url=_database_url(args.database_url),
                module_paths=installed_apps(args.installed_app),
                tenant_id=args.tenant_id,
                tenant_status=args.tenant_status,
                request_id_prefix=args.request_id_prefix,
                provider=_scheduler_provider(args.provider),
                payload=_parse_payload_json(args.payload_json),
                now=_parse_datetime(args.now),
                instance_id=args.instance_id,
                max_iterations=args.max_iterations,
                idle_sleep_seconds=_scheduler_idle_sleep_seconds(args.idle_sleep_seconds),
                lock_ttl_seconds=_scheduler_lock_ttl_seconds(args.lock_ttl_seconds),
            )
        )
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command=args.role, role="scheduler"),
            as_json=args.as_json,
        )
        return 1
    payload = {
        **result.to_dict(),
        "command": args.role,
        "role": "scheduler",
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_outbox_dispatcher_run(args: argparse.Namespace) -> int:
    try:
        result = asyncio.run(_run_outbox_dispatcher_loop(args))
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command=args.role, role="outbox-dispatcher"),
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


async def _run_outbox_dispatcher_loop(
    args: argparse.Namespace,
) -> OutboxDispatchRunResult:
    shutdown_event = asyncio.Event()
    cleanup_signal_handlers = _install_shutdown_signal_handlers(shutdown_event)
    try:
        return await run_outbox_dispatch_loop(
            database_url=_database_url(args.database_url),
            module_paths=installed_apps(args.installed_app),
            dispatcher_id=args.dispatcher_id,
            batch_size=args.batch_size,
            instance_id=args.instance_id,
            max_iterations=args.max_iterations,
            idle_sleep_seconds=args.idle_sleep_seconds,
            shutdown_event=shutdown_event,
        )
    finally:
        cleanup_signal_handlers()


def _install_shutdown_signal_handlers(
    shutdown_event: asyncio.Event,
) -> Callable[[], None]:
    cleanup_callbacks: list[Callable[[], None]] = []

    def request_shutdown(_signum: int, _frame: object) -> None:
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous_handler = signal.getsignal(sig)
            signal.signal(sig, request_shutdown)
        except (OSError, RuntimeError, ValueError):
            continue
        cleanup_callbacks.append(
            lambda sig=sig, previous_handler=previous_handler: signal.signal(
                sig,
                previous_handler,
            )
        )

    def cleanup() -> None:
        for callback in reversed(cleanup_callbacks):
            try:
                callback()
            except (OSError, RuntimeError, ValueError):
                continue

    return cleanup


async def _run_worker_once(
    *,
    database_url: str,
    app_modules: list[str],
    queue: str,
    provider: str,
    max_attempts: int,
    retry_backoff_seconds: int,
    tenant_status: str,
) -> dict[str, object]:
    app_registry = AppRegistry(
        app_modules,
        runtime_capabilities=resolve_runtime_capabilities(
            get_settings(),
            database_url=database_url,
            service_role="worker",
        ),
    ).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            if uow.session is None:
                return error_payload(
                    code=CLI_RUNTIME_ERROR,
                    message="database session was not initialized",
                    command="worker",
                    exit_code=1,
                    role="worker",
            )
            repository = TaskRunRepository(uow.session)
            if provider == "database":
                result = await DatabaseQueueTaskProvider(
                    task_registry,
                    task_repository=repository,
                    max_attempts=max_attempts,
                    retry_backoff_seconds=retry_backoff_seconds,
                ).run_next(
                    queue=queue,
                    tenant_status=tenant_status,  # type: ignore[arg-type]
                )
            else:
                task_run = await repository.claim_next_pending(queue=queue)
                result = None
                if task_run is not None:
                    result = await SyncTaskProvider(
                        task_registry,
                        task_repository=repository,
                        max_attempts=max_attempts,
                    ).run_task_run(
                        task_run,
                        tenant_status=tenant_status,  # type: ignore[arg-type]
                    )
            if result is None:
                return {"ok": True, "claimed": 0, "queue": queue, "task_result": None}
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
    provider: str | None = None,
) -> dict[str, object]:
    settings = get_settings()
    app_registry = AppRegistry(
        app_modules,
        runtime_capabilities=resolve_runtime_capabilities(
            settings,
            database_url=database_url,
            service_role="scheduler",
        ),
    ).load()
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
                return error_payload(
                    code=CLI_RUNTIME_ERROR,
                    message="database session was not initialized",
                    command="scheduler",
                    exit_code=1,
                    role="scheduler",
                )
            trigger_provider = LockedScheduleProvider(
                provider=ManualScheduleProvider(
                    schedule_registry=schedule_registry,
                    task_provider=_scheduler_task_provider(
                        settings=settings,
                        task_registry=task_registry,
                        repository=TaskRunRepository(uow.session),
                    ),
                    trigger_repository=ScheduleTriggerRepository(uow.session),
                ),
                lock_provider=MemoryLockProvider(),
                lock_ttl_seconds=lock_ttl_seconds,
            )
            trigger_provider = wrap_external_scheduler_provider(
                provider=_scheduler_provider(provider),
                schedule_registry=schedule_registry,
                trigger_provider=trigger_provider,
            )
            result = await trigger_provider.trigger(
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


def _task_queue_provider(value: str | None) -> str:
    return value or get_settings().task_queue.provider


def _task_queue_max_attempts(value: int | None) -> int:
    return value or get_settings().task_queue.max_attempts


def _task_queue_retry_backoff_seconds(value: int | None) -> int:
    return value if value is not None else get_settings().task_queue.retry_backoff_seconds


def _scheduler_idle_sleep_seconds(value: float | None) -> float:
    return value if value is not None else get_settings().scheduler.idle_sleep_seconds


def _scheduler_lock_ttl_seconds(value: int | None) -> int:
    return value if value is not None else get_settings().scheduler.lock_ttl_seconds


def _scheduler_provider(value: str | None) -> str:
    return value or get_settings().scheduler.provider


def _scheduler_task_provider(
    *,
    settings: Settings,
    task_registry: TaskRegistry,
    repository: TaskRunRepository,
) -> SyncTaskProvider | DatabaseQueueTaskProvider:
    if settings.task_queue.provider == "database":
        return DatabaseQueueTaskProvider(
            task_registry,
            task_repository=repository,
            max_attempts=settings.task_queue.max_attempts,
            retry_backoff_seconds=settings.task_queue.retry_backoff_seconds,
        )
    return SyncTaskProvider(
        task_registry,
        task_repository=repository,
        max_attempts=settings.task_queue.max_attempts,
    )


def _runtime_settings(
    *,
    installed_app_paths: list[str],
    database_url: str | None,
    service_role: str | None = None,
):
    settings = get_settings()
    updates: dict[str, object] = {"installed_apps": installed_app_paths}
    if database_url is not None:
        updates["database"] = settings.database.model_copy(update={"url": database_url})
    if service_role is not None:
        updates["observability"] = settings.observability.model_copy(
            update={"service_role": service_role}
        )
    return settings.model_copy(update=updates)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_payload_json(value: str) -> dict[str, object]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("payload-json must decode to an object")
    return dict(payload)


def _parse_actual_env(values: list[str]) -> dict[str, str]:
    actual: dict[str, str] = {}
    for value in values:
        key, item_value = _parse_env_assignment(value)
        actual[key] = item_value
    return actual


def _parse_role_actual_env(values: list[str]) -> dict[str, dict[str, str]]:
    actual: dict[str, dict[str, str]] = {}
    for value in values:
        if ":" not in value:
            raise ValueError(f"Role config mapping must use ROLE:KEY=VALUE format: {value}")
        role, assignment = value.split(":", 1)
        if role not in _PROCESS_ROLES:
            raise ValueError(f"Unknown process role in config mapping: {role}")
        key, item_value = _parse_env_assignment(assignment)
        actual.setdefault(role, {})[key] = item_value
    return actual


def _parse_env_assignment(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(f"Config mapping must use KEY=VALUE format: {value}")
    key, item_value = value.split("=", 1)
    if not key:
        raise ValueError(f"Config mapping must use KEY=VALUE format: {value}")
    return key, item_value
