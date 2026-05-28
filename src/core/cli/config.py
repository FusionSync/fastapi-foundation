from __future__ import annotations

import argparse
import os

from core.cli.common import CLI_USAGE_ERROR, error_payload, print_payload
from core.config import check_profile_drift, render_profile_template

_PROFILES = ["local", "private", "cloud"]


def register_config_commands(subparsers: argparse._SubParsersAction) -> None:
    config_parser = subparsers.add_parser("config")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    template_parser = config_subparsers.add_parser("template")
    template_parser.add_argument("--profile", choices=_PROFILES, required=True)
    template_parser.add_argument("--json", action="store_true", dest="as_json")
    template_parser.set_defaults(handler=_handle_config_template)

    drift_parser = config_subparsers.add_parser("drift-check")
    drift_parser.add_argument("--profile", choices=_PROFILES, required=True)
    drift_parser.add_argument("--actual", action="append", default=[])
    drift_parser.add_argument("--json", action="store_true", dest="as_json")
    drift_parser.set_defaults(handler=_handle_config_drift_check)


def _handle_config_template(args: argparse.Namespace) -> int:
    template = render_profile_template(args.profile)
    payload = {
        **template.to_dict(),
        "command": "config template",
    }
    print_payload(payload, as_json=args.as_json)
    return 0


def _handle_config_drift_check(args: argparse.Namespace) -> int:
    try:
        actual_env = _parse_actual_env(args.actual)
    except ValueError as exc:
        print_payload(
            error_payload(
                code=CLI_USAGE_ERROR,
                message=str(exc),
                command="config drift-check",
                exit_code=2,
            ),
            as_json=args.as_json,
        )
        return 2
    report = check_profile_drift(args.profile, actual_env)
    payload = {
        "ok": not report.has_drift,
        "command": "config drift-check",
        "profile": args.profile,
        "drift": report.to_dict(),
    }
    print_payload(payload, as_json=args.as_json)
    return 1 if report.has_drift else 0


def _parse_actual_env(values: list[str]) -> dict[str, str]:
    if not values:
        return dict(os.environ)
    actual: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Actual config mapping must use KEY=VALUE format: {value}")
        key, item_value = value.split("=", 1)
        if not key:
            raise ValueError(f"Actual config mapping must use KEY=VALUE format: {value}")
        actual[key] = item_value
    return actual
