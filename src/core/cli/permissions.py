from __future__ import annotations

import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps.registry import AppRegistry
from core.cli.common import (
    CLI_RUNTIME_ERROR,
    error_payload,
    exception_error_payload,
    installed_apps,
    print_payload,
)
from core.db import unit_of_work
from core.permissions import PermissionRegistry, PolicyProjector


def register_permission_commands(subparsers: argparse._SubParsersAction) -> None:
    permissions_parser = subparsers.add_parser("permissions")
    permissions_subparsers = permissions_parser.add_subparsers(
        dest="permissions_command",
        required=True,
    )
    for command in ("catalog", "reconcile"):
        command_parser = permissions_subparsers.add_parser(command)
        command_parser.add_argument("--installed-app", action="append", default=[])
        command_parser.add_argument("--json", action="store_true", dest="as_json")
        if command == "reconcile":
            command_parser.add_argument("--database-url")
            command_parser.add_argument("--repair", action="store_true")
        command_parser.set_defaults(handler=_handle_permissions)


def _handle_permissions(args: argparse.Namespace) -> int:
    if args.permissions_command == "reconcile" and args.database_url:
        payload = asyncio.run(
            _reconcile_projection(database_url=args.database_url, repair=args.repair)
        )
        print_payload(payload, as_json=args.as_json)
        return 0 if bool(payload.get("ok")) else 1

    try:
        app_registry = AppRegistry(installed_apps(args.installed_app)).load()
        permission_registry = PermissionRegistry.from_app_registry(app_registry)
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command=f"permissions {args.permissions_command}"),
            as_json=args.as_json,
        )
        return 1
    payload = permission_registry.to_dict()
    if args.permissions_command == "reconcile":
        payload = {
            **payload,
            "reconciled": permission_registry.errors == [],
            "mode": "metadata",
        }
    print_payload(payload, as_json=args.as_json)
    return 0 if bool(payload.get("ok")) else 1


async def _reconcile_projection(*, database_url: str, repair: bool) -> dict[str, object]:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            if uow.session is None:
                return error_payload(
                    code=CLI_RUNTIME_ERROR,
                    message="database session was not initialized",
                    command="permissions reconcile",
                    exit_code=1,
                    mode="projection",
                )
            result = await PolicyProjector(uow.session).reconcile(repair=repair)
            return {**result.to_dict(), "mode": "projection"}
    finally:
        await engine.dispose()
