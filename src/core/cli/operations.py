from __future__ import annotations

import argparse

from core.cli.common import print_payload
from core.operations import (
    check_backup_readiness,
    check_config,
    check_process_health,
    run_deployment_smoke,
)
from core.operations.backup import parse_backup_time

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
    result = check_process_health(role)
    payload = {
        **result.to_dict(),
        "command": args.role,
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1
