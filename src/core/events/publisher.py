from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class EventPublisher(Protocol):
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
        """Publish a reliable domain event without exposing the transport."""
