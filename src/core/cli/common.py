from __future__ import annotations

import json
from typing import Any

from core.config import get_settings

CLI_CONFIRMATION_REQUIRED = "CLI_CONFIRMATION_REQUIRED"
CLI_RUNTIME_ERROR = "CLI_RUNTIME_ERROR"
CLI_USAGE_ERROR = "CLI_USAGE_ERROR"


def installed_apps(overrides: list[str]) -> list[str]:
    return overrides or get_settings().installed_apps


def error_payload(
    *,
    code: str,
    message: str,
    exit_code: int,
    command: str | None = None,
    details: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": False,
        "exit_code": exit_code,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }
    if command is not None:
        payload["command"] = command
    payload.update(extra)
    return payload


def exception_error_payload(
    exc: Exception,
    *,
    command: str,
    exit_code: int = 1,
    **extra: object,
) -> dict[str, object]:
    return error_payload(
        code=CLI_RUNTIME_ERROR,
        message=f"{type(exc).__name__}: {exc}",
        command=command,
        exit_code=exit_code,
        details={"exception_type": type(exc).__name__},
        **extra,
    )


def print_payload(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)
