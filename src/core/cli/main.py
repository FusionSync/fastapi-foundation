from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from core.apps.conformance import check_app, check_apps
from core.apps.registry import AppRegistry
from core.config import get_settings
from core.migrations import (
    MigrationManifest,
    MigrationRegistry,
    check_drift,
    plan_migrations,
    run_preflight,
)
from core.operations import (
    check_backup_readiness,
    check_config,
    check_process_health,
    run_deployment_smoke,
)
from core.operations.backup import parse_backup_time
from core.permissions import PermissionRegistry


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="core")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_app_parser = subparsers.add_parser("check-app")
    check_app_parser.add_argument("module_path", nargs="?")
    check_app_parser.add_argument("--all", action="store_true")
    check_app_parser.add_argument("--installed-app", action="append", default=[])
    check_app_parser.add_argument("--json", action="store_true", dest="as_json")
    check_app_parser.set_defaults(handler=_handle_check_app)

    list_apps_parser = subparsers.add_parser("list-apps")
    list_apps_parser.add_argument("--installed-app", action="append", default=[])
    list_apps_parser.add_argument("--json", action="store_true", dest="as_json")
    list_apps_parser.set_defaults(handler=_handle_list_apps)

    migrate_parser = subparsers.add_parser("migrate")
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_command", required=True)
    for command in ("plan", "preflight", "dry-run", "status"):
        command_parser = migrate_subparsers.add_parser(command)
        command_parser.add_argument("--installed-app", action="append", default=[])
        command_parser.add_argument("--json", action="store_true", dest="as_json")
        if command == "preflight":
            command_parser.add_argument("--backup-ready", action="store_true")
        command_parser.set_defaults(handler=_handle_migrate)
    apply_parser = migrate_subparsers.add_parser("apply")
    apply_parser.add_argument("--installed-app", action="append", default=[])
    apply_parser.add_argument("--json", action="store_true", dest="as_json")
    apply_parser.add_argument("--yes", action="store_true")
    apply_parser.set_defaults(handler=_handle_migrate)
    drift_parser = migrate_subparsers.add_parser("drift-check")
    drift_parser.add_argument("--expected", action="append", default=[])
    drift_parser.add_argument("--actual", action="append", default=[])
    drift_parser.add_argument("--json", action="store_true", dest="as_json")
    drift_parser.set_defaults(handler=_handle_migrate_drift_check)

    check_config_parser = subparsers.add_parser("check-config")
    check_config_parser.add_argument(
        "--profile",
        choices=["local", "private", "cloud"],
        required=True,
    )
    check_config_parser.add_argument("--json", action="store_true", dest="as_json")
    check_config_parser.set_defaults(handler=_handle_check_config)

    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--profile", choices=["local", "private", "cloud"], required=True)
    smoke_parser.add_argument("--json", action="store_true", dest="as_json")
    smoke_parser.set_defaults(handler=_handle_smoke)

    backup_parser = subparsers.add_parser("backup-check")
    backup_parser.add_argument("--profile", choices=["local", "private", "cloud"], required=True)
    backup_parser.add_argument("--latest-backup-at")
    backup_parser.add_argument("--max-age-hours", type=int)
    backup_parser.add_argument("--json", action="store_true", dest="as_json")
    backup_parser.set_defaults(handler=_handle_backup_check)

    for role in ("serve", "worker", "scheduler", "outbox-dispatcher"):
        role_parser = subparsers.add_parser(role)
        role_parser.add_argument("--json", action="store_true", dest="as_json")
        role_parser.set_defaults(handler=_handle_process_role, role=role)

    permissions_parser = subparsers.add_parser("permissions")
    permissions_subparsers = permissions_parser.add_subparsers(
        dest="permissions_command",
        required=True,
    )
    for command in ("catalog", "reconcile"):
        command_parser = permissions_subparsers.add_parser(command)
        command_parser.add_argument("--installed-app", action="append", default=[])
        command_parser.add_argument("--json", action="store_true", dest="as_json")
        command_parser.set_defaults(handler=_handle_permissions)
    return parser


def _installed_apps(overrides: list[str]) -> list[str]:
    return overrides or get_settings().installed_apps


def _handle_check_app(args: argparse.Namespace) -> int:
    if args.all:
        module_paths = _installed_apps(args.installed_app)
        results = check_apps(module_paths)
        ok = all(result.ok for result in results)
        payload: object = {"ok": ok, "apps": [result.to_dict() for result in results]}
    else:
        if not args.module_path:
            payload = {
                "ok": False,
                "error": "check-app requires module_path unless --all is used",
            }
            _print_payload(payload, as_json=args.as_json)
            return 2
        result = check_app(args.module_path)
        ok = result.ok
        payload = result.to_dict()

    _print_payload(payload, as_json=args.as_json)
    return 0 if ok else 1


def _handle_list_apps(args: argparse.Namespace) -> int:
    module_paths = _installed_apps(args.installed_app)
    try:
        registry = AppRegistry(module_paths).load()
    except Exception as exc:
        _print_payload(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "apps": [],
            },
            as_json=args.as_json,
        )
        return 1
    payload = {
        "ok": True,
        "apps": [
            {
                "label": module.label,
                "version": module.version,
                "dependencies": module.dependencies,
                "routers": len(module.routers),
                "permissions": [
                    {
                        "resource": permission.resource,
                        "action": permission.action,
                        "scope": permission.scope,
                    }
                    for permission in module.permissions
                ],
            }
            for module in registry.modules
        ],
    }
    _print_payload(payload, as_json=args.as_json)
    return 0


def _handle_migrate(args: argparse.Namespace) -> int:
    try:
        app_registry = AppRegistry(_installed_apps(args.installed_app)).load()
        migration_registry = MigrationRegistry.from_app_registry(app_registry)
    except Exception as exc:
        _print_payload({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, as_json=args.as_json)
        return 1

    if args.migrate_command == "plan":
        plan = plan_migrations(migration_registry.manifests, app_registry=app_registry)
        payload = {
            **plan.to_dict(),
            "ok": not migration_registry.errors and plan.ok,
            "registry_errors": migration_registry.errors,
        }
    elif args.migrate_command == "preflight":
        result = run_preflight(
            migration_registry.manifests,
            backup_ready=args.backup_ready,
        )
        payload = {
            **result.to_dict(),
            "ok": not migration_registry.errors and result.ok,
            "registry_errors": migration_registry.errors,
        }
    elif args.migrate_command == "dry-run":
        plan = plan_migrations(migration_registry.manifests, app_registry=app_registry)
        payload = {
            **plan.to_dict(),
            "ok": not migration_registry.errors and plan.ok,
            "dry_run": True,
            "registry_errors": migration_registry.errors,
        }
    elif args.migrate_command == "status":
        payload = {
            "ok": not migration_registry.errors,
            "registry_errors": migration_registry.errors,
            "apps": _migration_status(migration_registry.manifests),
        }
    else:
        payload = {
            "ok": False,
            "error": "migrate apply is intentionally gated; use dry-run/preflight first",
        }

    _print_payload(payload, as_json=args.as_json)
    return 0 if bool(payload.get("ok")) else 1


def _handle_migrate_drift_check(args: argparse.Namespace) -> int:
    report = check_drift(_parse_heads(args.expected), _parse_heads(args.actual))
    payload = {"ok": not report.has_drift, "drift": report.to_dict()}
    _print_payload(payload, as_json=args.as_json)
    return 0 if not report.has_drift else 1


def _handle_check_config(args: argparse.Namespace) -> int:
    result = check_config(args.profile)
    _print_payload(result.to_dict(), as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_smoke(args: argparse.Namespace) -> int:
    result = run_deployment_smoke(args.profile)
    _print_payload(result.to_dict(), as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_backup_check(args: argparse.Namespace) -> int:
    result = check_backup_readiness(
        profile=args.profile,
        latest_backup_at=parse_backup_time(args.latest_backup_at),
        max_age_hours=args.max_age_hours,
    )
    _print_payload(result.to_dict(), as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_process_role(args: argparse.Namespace) -> int:
    role = "server" if args.role == "serve" else args.role
    result = check_process_health(role)
    payload = {
        **result.to_dict(),
        "command": args.role,
    }
    _print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_permissions(args: argparse.Namespace) -> int:
    try:
        app_registry = AppRegistry(_installed_apps(args.installed_app)).load()
        permission_registry = PermissionRegistry.from_app_registry(app_registry)
    except Exception as exc:
        _print_payload({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, as_json=args.as_json)
        return 1
    payload = permission_registry.to_dict()
    if args.permissions_command == "reconcile":
        payload = {
            **payload,
            "reconciled": permission_registry.errors == [],
            "mode": "metadata",
        }
    _print_payload(payload, as_json=args.as_json)
    return 0 if bool(payload.get("ok")) else 1


def _migration_status(manifests: list[MigrationManifest]) -> list[dict[str, object]]:
    heads: dict[str, str] = {}
    for manifest in manifests:
        heads[manifest.app_label] = manifest.migration_id
    return [
        {"app_label": app_label, "head": head}
        for app_label, head in sorted(heads.items())
    ]


def _parse_heads(values: list[str]) -> dict[str, str]:
    heads: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Head mapping must use app=head format: {value}")
        app_label, head = value.split("=", 1)
        heads[app_label] = head
    return heads


def _print_payload(payload: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)


if __name__ == "__main__":
    sys.exit(main())
