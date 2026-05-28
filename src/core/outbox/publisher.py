from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.outbox.repository import OutboxRepository


class OutboxEventPublisher:
    def __init__(self, repository: OutboxRepository) -> None:
        self.repository = repository

    async def publish(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        tenant_id: str,
        payload: Mapping[str, Any],
        event_version: int = 1,
        max_attempts: int = 3,
    ) -> None:
        await self.repository.add(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            tenant_id=tenant_id,
            payload=dict(payload),
            event_version=event_version,
            max_attempts=max_attempts,
        )
