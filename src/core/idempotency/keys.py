from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def hash_request_payload(payload: Any) -> str:
    if isinstance(payload, bytes):
        encoded = payload
    elif isinstance(payload, str):
        encoded = payload.encode("utf-8")
    else:
        encoded = json.dumps(
            payload,
            default=_json_default,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal | UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
