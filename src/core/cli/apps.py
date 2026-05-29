from __future__ import annotations

import argparse

from core.apps import resolve_runtime_capabilities
from core.apps.bootstrap import AppBootstrapError, bootstrap_app
from core.apps.conformance import check_app, check_apps
from core.apps.registry import AppRegistry
from core.cli.common import (
    CLI_RUNTIME_ERROR,
    CLI_USAGE_ERROR,
    error_payload,
    exception_error_payload,
    installed_apps,
    print_payload,
)
from core.config import get_settings


def register_app_commands(subparsers: argparse._SubParsersAction) -> None:
    bootstrap_app_parser = subparsers.add_parser("bootstrap-app")
    bootstrap_app_parser.add_argument("label")
    bootstrap_app_parser.add_argument("--target-root", default="src")
    bootstrap_app_parser.add_argument("--package", default="apps")
    bootstrap_app_parser.add_argument("--json", action="store_true", dest="as_json")
    bootstrap_app_parser.set_defaults(handler=_handle_bootstrap_app)

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


def _handle_bootstrap_app(args: argparse.Namespace) -> int:
    try:
        result = bootstrap_app(
            args.label,
            target_root=args.target_root,
            package=args.package,
        )
    except AppBootstrapError as exc:
        print_payload(
            error_payload(
                code=CLI_RUNTIME_ERROR,
                message=str(exc),
                command="bootstrap-app",
                exit_code=1,
            ),
            as_json=args.as_json,
        )
        return 1

    print_payload(result.to_dict(), as_json=args.as_json)
    return 0


def _handle_check_app(args: argparse.Namespace) -> int:
    if args.all:
        module_paths = installed_apps(args.installed_app)
        results = check_apps(module_paths)
        ok = all(result.ok for result in results)
        payload: object = {"ok": ok, "apps": [result.to_dict() for result in results]}
    else:
        if not args.module_path:
            payload = error_payload(
                code=CLI_USAGE_ERROR,
                message="check-app requires module_path unless --all is used",
                command="check-app",
                exit_code=2,
            )
            print_payload(payload, as_json=args.as_json)
            return 2
        result = check_app(args.module_path)
        ok = result.ok
        payload = result.to_dict()

    print_payload(payload, as_json=args.as_json)
    return 0 if ok else 1


def _handle_list_apps(args: argparse.Namespace) -> int:
    module_paths = installed_apps(args.installed_app)
    settings = get_settings()
    try:
        registry = AppRegistry(
            module_paths,
            runtime_capabilities=resolve_runtime_capabilities(settings),
        ).load()
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command="list-apps", apps=[]),
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
                "min_core_version": module.min_core_version,
                "required_capabilities": module.required_capabilities,
                "provided_capabilities": module.provided_capabilities,
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
        "diagnostics": registry.diagnostics.to_dict(),
    }
    print_payload(payload, as_json=args.as_json)
    return 0
