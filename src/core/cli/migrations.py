from __future__ import annotations

import argparse
import asyncio
import uuid

from core.apps import AppRegistry, resolve_runtime_capabilities
from core.cli.common import (
    CLI_CONFIRMATION_REQUIRED,
    CLI_USAGE_ERROR,
    error_payload,
    exception_error_payload,
    installed_apps,
    print_payload,
)
from core.config import get_settings
from core.locks import MemoryLockProvider
from core.migrations import (
    AlembicMigrationExecutor,
    MigrationManifest,
    MigrationRegistry,
    apply_migration_metadata,
    apply_migrations,
    check_drift,
    dry_run_migration_metadata,
    plan_migrations,
    run_preflight,
)

_MIGRATION_PHASES = ["expand", "backfill", "contract", "maintenance"]


def register_migration_commands(subparsers: argparse._SubParsersAction) -> None:
    migrate_parser = subparsers.add_parser("migrate")
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_command", required=True)
    for command in ("plan", "preflight", "dry-run", "status"):
        command_parser = migrate_subparsers.add_parser(command)
        command_parser.add_argument("--installed-app", action="append", default=[])
        command_parser.add_argument("--json", action="store_true", dest="as_json")
        if command != "status":
            command_parser.add_argument("--phase", choices=_MIGRATION_PHASES)
        if command in {"preflight", "dry-run"}:
            command_parser.add_argument("--backup-ready", action="store_true")
        command_parser.set_defaults(handler=_handle_migrate)

    apply_parser = migrate_subparsers.add_parser("apply")
    apply_parser.add_argument("--installed-app", action="append", default=[])
    apply_parser.add_argument("--json", action="store_true", dest="as_json")
    apply_parser.add_argument("--backup-ready", action="store_true")
    apply_parser.add_argument("--yes", action="store_true")
    apply_parser.add_argument("--alembic-config")
    apply_parser.add_argument("--database-url")
    apply_parser.add_argument("--script-location")
    apply_parser.add_argument("--lock-owner")
    apply_parser.add_argument("--lock-ttl-seconds", type=int, default=300)
    apply_parser.add_argument("--phase", choices=_MIGRATION_PHASES)
    apply_parser.set_defaults(handler=_handle_migrate)

    run_parser = migrate_subparsers.add_parser("run")
    run_parser.add_argument("--installed-app", action="append", default=[])
    run_parser.add_argument("--json", action="store_true", dest="as_json")
    run_parser.add_argument("--backup-ready", action="store_true")
    run_parser.add_argument("--apply", action="store_true")
    run_parser.add_argument("--yes", action="store_true")
    run_parser.add_argument("--alembic-config")
    run_parser.add_argument("--database-url")
    run_parser.add_argument("--script-location")
    run_parser.add_argument("--lock-owner")
    run_parser.add_argument("--lock-ttl-seconds", type=int, default=300)
    run_parser.add_argument("--phase", choices=_MIGRATION_PHASES)
    run_parser.set_defaults(handler=_handle_migrate_run)

    drift_parser = migrate_subparsers.add_parser("drift-check")
    drift_parser.add_argument("--expected", action="append", default=[])
    drift_parser.add_argument("--actual", action="append", default=[])
    drift_parser.add_argument("--json", action="store_true", dest="as_json")
    drift_parser.set_defaults(handler=_handle_migrate_drift_check)


def _handle_migrate(args: argparse.Namespace) -> int:
    try:
        app_registry = _load_migration_app_registry(args)
        migration_registry = MigrationRegistry.from_app_registry(app_registry)
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command=f"migrate {args.migrate_command}"),
            as_json=args.as_json,
        )
        return 1

    if args.migrate_command == "plan":
        plan = plan_migrations(
            migration_registry.manifests,
            app_registry=app_registry,
            phase=args.phase,
        )
        payload = {
            **plan.to_dict(),
            "ok": not migration_registry.errors and plan.ok,
            "registry_errors": migration_registry.errors,
        }
    elif args.migrate_command == "preflight":
        result = run_preflight(
            migration_registry.manifests,
            backup_ready=args.backup_ready,
            phase=args.phase,
        )
        payload = {
            **result.to_dict(),
            "ok": not migration_registry.errors and result.ok,
            "registry_errors": migration_registry.errors,
        }
    elif args.migrate_command == "dry-run":
        result = run_preflight(
            migration_registry.manifests,
            backup_ready=args.backup_ready,
            phase=args.phase,
        )
        dry_run_result = dry_run_migration_metadata(result)
        payload = {
            **dry_run_result.to_dict(),
            "ok": not migration_registry.errors and dry_run_result.ok,
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


def _handle_migrate_run(args: argparse.Namespace) -> int:
    try:
        app_registry = _load_migration_app_registry(args)
        migration_registry = MigrationRegistry.from_app_registry(app_registry)
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command="migrate run", role="migrate"),
            as_json=args.as_json,
        )
        return 1

    plan = plan_migrations(
        migration_registry.manifests,
        app_registry=app_registry,
        phase=args.phase,
    )
    plan_payload = {
        **plan.to_dict(),
        "ok": not migration_registry.errors and plan.ok,
        "registry_errors": migration_registry.errors,
    }
    preflight = run_preflight(
        migration_registry.manifests,
        backup_ready=args.backup_ready,
        phase=args.phase,
    )
    preflight_payload = {
        **preflight.to_dict(),
        "ok": not migration_registry.errors and preflight.ok,
        "registry_errors": migration_registry.errors,
    }
    if args.apply:
        final_stage = "apply"
        final_payload = _apply_migrations(args, migration_registry)
    else:
        final_stage = "dry-run"
        dry_run_result = dry_run_migration_metadata(preflight)
        final_payload = {
            **dry_run_result.to_dict(),
            "ok": not migration_registry.errors and dry_run_result.ok,
            "registry_errors": migration_registry.errors,
        }
    stages = [
        {"name": "plan", "ok": bool(plan_payload.get("ok")), "result": plan_payload},
        {"name": "preflight", "ok": bool(preflight_payload.get("ok")), "result": preflight_payload},
        {"name": final_stage, "ok": bool(final_payload.get("ok")), "result": final_payload},
    ]
    payload = {
        "ok": all(stage["ok"] for stage in stages),
        "command": "migrate",
        "role": "migrate",
        "mode": "apply" if args.apply else "dry-run",
        "stages": stages,
    }
    print_payload(payload, as_json=args.as_json)
    return 0 if bool(payload.get("ok")) else 1


def _apply_migrations(
    args: argparse.Namespace,
    migration_registry: MigrationRegistry,
) -> dict[str, object]:
    if not args.yes:
        return error_payload(
            code=CLI_CONFIRMATION_REQUIRED,
            message="migrate apply requires --yes",
            command="migrate apply",
            exit_code=1,
        )
    if migration_registry.errors:
        return {
            "ok": False,
            "applied": False,
            "mode": "registry",
            "migrations": [],
            "errors": migration_registry.errors,
            "warnings": [],
            "registry_errors": migration_registry.errors,
        }
    result = run_preflight(
        migration_registry.manifests,
        backup_ready=args.backup_ready,
        phase=args.phase,
    )
    if args.alembic_config:
        apply_result = asyncio.run(
            apply_migrations(
                result,
                executor=AlembicMigrationExecutor(
                    config_path=args.alembic_config,
                    database_url=args.database_url,
                    script_location=args.script_location,
                ),
                lock_provider=MemoryLockProvider(),
                owner_token=args.lock_owner or f"migrate-cli:{uuid.uuid4()}",
                lock_ttl_seconds=args.lock_ttl_seconds,
            )
        )
    else:
        apply_result = apply_migration_metadata(result)
    return {
        **apply_result.to_dict(),
        "ok": not migration_registry.errors and apply_result.ok,
        "registry_errors": migration_registry.errors,
    }


def _load_migration_app_registry(args: argparse.Namespace) -> AppRegistry:
    return AppRegistry(
        installed_apps(args.installed_app),
        runtime_capabilities=resolve_runtime_capabilities(
            get_settings(),
            database_url=getattr(args, "database_url", None),
            service_role="migrate",
        ),
    ).load()


def _handle_migrate_drift_check(args: argparse.Namespace) -> int:
    try:
        report = check_drift(_parse_heads(args.expected), _parse_heads(args.actual))
    except ValueError as exc:
        print_payload(
            error_payload(
                code=CLI_USAGE_ERROR,
                message=str(exc),
                command="migrate drift-check",
                exit_code=2,
            ),
            as_json=args.as_json,
        )
        return 2
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
            raise ValueError(f"Head mapping must use app=head format: {value}")
        app_label, head = value.split("=", 1)
        heads[app_label] = head
    return heads
