from __future__ import annotations

import argparse
import asyncio

from core.cli.common import installed_apps, print_payload
from core.config import get_settings
from core.operations import (
    check_backup_readiness,
    check_config,
    check_process_health,
    run_deployment_smoke,
)
from core.operations.backup import parse_backup_time
from core.outbox import run_outbox_dispatch_loop

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
    if role == "outbox-dispatcher" and args.run:
        return _handle_outbox_dispatcher_run(args)
    result = check_process_health(role)
    payload = {
        **result.to_dict(),
        "command": args.role,
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1


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


def _database_url(value: str | None) -> str:
    return value or get_settings().database.url
