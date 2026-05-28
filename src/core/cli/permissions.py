from __future__ import annotations

import argparse

from core.apps.registry import AppRegistry
from core.cli.common import installed_apps, print_payload
from core.permissions import PermissionRegistry


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
        command_parser.set_defaults(handler=_handle_permissions)


def _handle_permissions(args: argparse.Namespace) -> int:
    try:
        app_registry = AppRegistry(installed_apps(args.installed_app)).load()
        permission_registry = PermissionRegistry.from_app_registry(app_registry)
    except Exception as exc:
        print_payload(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
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
