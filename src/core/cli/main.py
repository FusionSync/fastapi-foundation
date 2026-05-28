from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from core.cli.apps import register_app_commands
from core.cli.migrations import register_migration_commands
from core.cli.operations import register_operation_commands
from core.cli.outbox import register_outbox_commands
from core.cli.permissions import register_permission_commands


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="core")
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_app_commands(subparsers)
    register_migration_commands(subparsers)
    register_operation_commands(subparsers)
    register_outbox_commands(subparsers)
    register_permission_commands(subparsers)
    return parser


if __name__ == "__main__":
    sys.exit(main())
