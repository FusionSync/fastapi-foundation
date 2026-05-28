from __future__ import annotations

from typing import Any

from core.base import BaseSchema


class AuditLogRead(BaseSchema):
    id: str
    tenant_id: str | None = None
    actor_id: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    result: str
    reason: str | None = None
    policy_version: int | None = None
    request_id: str | None = None
    trace_id: str | None = None
    route: str | None = None
    method: str | None = None
    payload: dict[str, Any]
    hash_prev: str | None = None
    hash: str
