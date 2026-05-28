from __future__ import annotations

import json
from typing import Any

from core.config import get_settings


def installed_apps(overrides: list[str]) -> list[str]:
    return overrides or get_settings().installed_apps


def print_payload(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)
