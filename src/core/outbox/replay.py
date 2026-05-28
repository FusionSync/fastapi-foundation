from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.outbox.models import OutboxEvent
from core.outbox.repository import OutboxRepository


@dataclass(frozen=True, slots=True)
class ReplayDeadLetterResult:
    ok: bool
    event_id: str
    status: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"ok": self.ok, "event_id": self.event_id}
        if self.status is not None:
            payload["status"] = self.status
        if self.error is not None:
            payload["error"] = self.error
        return payload


async def list_dead_letter_events(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> list[dict[str, object]]:
    result = await session.execute(
        select(OutboxEvent)
        .where(OutboxEvent.status == "dead_letter")
        .order_by(OutboxEvent.created_at.asc())
        .limit(limit)
    )
    return [_event_to_dict(event) for event in result.scalars().all()]


async def replay_dead_letter_by_id(
    session: AsyncSession,
    *,
    event_id: str,
) -> ReplayDeadLetterResult:
    event = await session.get(OutboxEvent, event_id)
    if event is None:
        return ReplayDeadLetterResult(ok=False, event_id=event_id, error="outbox event not found")
    try:
        await OutboxRepository(session).replay_dead_letter(event)
    except AppError as exc:
        return ReplayDeadLetterResult(ok=False, event_id=event_id, error=exc.message)
    return ReplayDeadLetterResult(ok=True, event_id=event_id, status=event.status)


def _event_to_dict(event: OutboxEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "tenant_id": event.tenant_id,
        "event_type": event.event_type,
        "event_version": event.event_version,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "status": event.status,
        "attempt_count": event.attempt_count,
        "max_attempts": event.max_attempts,
        "last_error": event.last_error,
        "dead_letter_reason": event.dead_letter_reason,
    }
