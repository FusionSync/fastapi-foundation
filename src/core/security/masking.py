from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REDACTED = "***REDACTED***"
DEFAULT_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "password",
        "password_hash",
        "refresh_token",
        "secret",
        "token",
    }
)


def redact_sensitive_data(
    value: Any,
    *,
    sensitive_keys: set[str] | frozenset[str] = DEFAULT_SENSITIVE_KEYS,
) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED
            if str(key).lower() in sensitive_keys
            else redact_sensitive_data(item, sensitive_keys=sensitive_keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item, sensitive_keys=sensitive_keys) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_data(item, sensitive_keys=sensitive_keys) for item in value]
    return value
