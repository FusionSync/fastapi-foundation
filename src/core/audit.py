from __future__ import annotations

from typing import Any, Protocol


class AuditRecorder(Protocol):
    async def record(
        self,
        *,
        action: str,
        resource_type: str,
        result: str,
        tenant_id: str | None = None,
        actor_id: str | None = None,
        resource_id: str | None = None,
        reason: str | None = None,
        policy_version: int | None = None,
        request_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> object:
        raise NotImplementedError
