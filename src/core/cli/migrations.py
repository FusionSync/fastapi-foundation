from __future__ import annotations

import argparse

from core.apps.registry import AppRegistry
from core.cli.common import installed_apps, print_payload
from core.migrations import (
    MigrationManifest,
    MigrationRegistry,
    apply_migration_metadata,
    check_drift,
    plan_migrations,
    run_preflight,
)


def register_migration_commands(subparsers: argparse._SubParsersAction) -> None:
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
    apply_parser.add_argument("--backup-ready", action="store_true")
    apply_parser.add_argument("--yes", action="store_true")
    apply_parser.set_defaults(handler=_handle_migrate)

    drift_parser = migrate_subparsers.add_parser("drift-check")
    drift_parser.add_argument("--expected", action="append", default=[])
    drift_parser.add_argument("--actual", action="append", default=[])
    drift_parser.add_argument("--json", action="store_true", dest="as_json")
    drift_parser.set_defaults(handler=_handle_migrate_drift_check)


def _handle_migrate(args: argparse.Namespace) -> int:
    try:
        app_registry = AppRegistry(installed_apps(args.installed_app)).load()
        migration_registry = MigrationRegistry.from_app_registry(app_registry)
    except Exception as exc:
        print_payload(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            as_json=args.as_json,
        )
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
        payload = _apply_migrations(args, migration_registry)

    print_payload(payload, as_json=args.as_json)
    return 0 if bool(payload.get("ok")) else 1


def _apply_migrations(
    args: argparse.Namespace,
    migration_registry: MigrationRegistry,
) -> dict[str, object]:
    if not args.yes:
        return {
            "ok": False,
            "error": "migrate apply requires --yes",
        }
    result = run_preflight(
        migration_registry.manifests,
        backup_ready=args.backup_ready,
    )
    apply_result = apply_migration_metadata(result)
    return {
        **apply_result.to_dict(),
        "ok": not migration_registry.errors and apply_result.ok,
        "registry_errors": migration_registry.errors,
    }


def _handle_migrate_drift_check(args: argparse.Namespace) -> int:
    report = check_drift(_parse_heads(args.expected), _parse_heads(args.actual))
    payload = {"ok": not report.has_drift, "drift": report.to_dict()}
    print_payload(payload, as_json=args.as_json)
    return 0 if not report.has_drift else 1


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
