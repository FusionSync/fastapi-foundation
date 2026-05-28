from __future__ import annotations

import argparse

from core.cli.common import print_payload
from core.config import render_profile_template

_PROFILES = ["local", "private", "cloud"]


def register_config_commands(subparsers: argparse._SubParsersAction) -> None:
    config_parser = subparsers.add_parser("config")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    template_parser = config_subparsers.add_parser("template")
    template_parser.add_argument("--profile", choices=_PROFILES, required=True)
    template_parser.add_argument("--json", action="store_true", dest="as_json")
    template_parser.set_defaults(handler=_handle_config_template)


def _handle_config_template(args: argparse.Namespace) -> int:
    template = render_profile_template(args.profile)
    payload = {
        **template.to_dict(),
        "command": "config template",
    }
    print_payload(payload, as_json=args.as_json)
    return 0
