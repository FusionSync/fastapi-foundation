from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from core.cli.apps import register_app_commands
from core.cli.common import CLI_RUNTIME_ERROR, CLI_USAGE_ERROR, error_payload, print_payload
from core.cli.config import register_config_commands
from core.cli.i18n import register_i18n_commands
from core.cli.idempotency import register_idempotency_commands
from core.cli.migrations import register_migration_commands
from core.cli.mq import register_mq_commands
from core.cli.operations import register_operation_commands
from core.cli.outbox import register_outbox_commands
from core.cli.permissions import register_permission_commands
from core.cli.tasks import register_task_commands


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    try:
        args = parser.parse_args(args_list)
        return args.handler(args)
    except CliUsageError as exc:
        if _wants_json(args_list):
            print_payload(
                error_payload(
                    code=CLI_USAGE_ERROR,
                    message=exc.message,
                    command=_command_path(args_list),
                    exit_code=exc.status,
                ),
                as_json=True,
            )
        else:
            print(f"{parser.prog}: error: {exc.message}", file=sys.stderr)
        return exc.status
    except Exception as exc:
        if _wants_json(args_list):
            print_payload(
                error_payload(
                    code=CLI_RUNTIME_ERROR,
                    message=f"{type(exc).__name__}: {exc}",
                    command=_command_path(args_list),
                    exit_code=1,
                    details={"exception_type": type(exc).__name__},
                ),
                as_json=True,
            )
        else:
            print(f"{parser.prog}: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = CliArgumentParser(prog="core")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=CliArgumentParser,
    )
    register_app_commands(subparsers)
    register_config_commands(subparsers)
    register_i18n_commands(subparsers)
    register_idempotency_commands(subparsers)
    register_migration_commands(subparsers)
    register_mq_commands(subparsers)
    register_operation_commands(subparsers)
    register_outbox_commands(subparsers)
    register_permission_commands(subparsers)
    register_task_commands(subparsers)
    return parser


class CliUsageError(Exception):
    def __init__(self, message: str, *, status: int = 2) -> None:
        super().__init__(message)
        self.message = _clean_argparse_message(message)
        self.status = status


class CliArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message, status=2)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if status:
            raise CliUsageError(message or "", status=status)
        raise SystemExit(status)


def _wants_json(argv: list[str]) -> bool:
    return "--json" in argv


def _command_path(argv: list[str]) -> str | None:
    command_parts: list[str] = []
    for item in argv:
        if item.startswith("-"):
            break
        command_parts.append(item)
    return " ".join(command_parts) if command_parts else None


def _clean_argparse_message(message: str) -> str:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return "invalid command arguments"
    last = lines[-1]
    if ": error: " in last:
        return last.split(": error: ", 1)[1]
    return last


if __name__ == "__main__":
    sys.exit(main())
