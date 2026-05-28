from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import EventEnvelope, EventRegistry
from core.exceptions import AppError
from core.outbox.models import OutboxEvent


class OutboxRepository:
    def __init__(self, session: AsyncSession, *, registry: EventRegistry | None = None) -> None:
        self.session = session
        self.registry = registry

    async def add(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        tenant_id: str,
        event_version: int = 1,
        max_attempts: int = 3,
    ) -> OutboxEvent:
        self._validate_event(event_type, event_version, payload, tenant_id)
        event = OutboxEvent(
            tenant_id=tenant_id,
            event_type=event_type,
            event_version=event_version,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            max_attempts=max_attempts,
            status="pending",
        )
        self.session.add(event)
        return event

    async def claim_batch(
        self,
        *,
        dispatcher_id: str,
        batch_size: int,
        lock_seconds: int = 60,
        now: datetime | None = None,
    ) -> list[OutboxEvent]:
        resolved_now = now or datetime.now(UTC)
        statement = (
            self._eligible_statement(resolved_now)
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
        )
        result = await self.session.execute(statement)
        events = list(result.scalars().all())
        lock_until = resolved_now + timedelta(seconds=lock_seconds)
        for event in events:
            event.status = "publishing"
            event.locked_by = dispatcher_id
            event.locked_until = lock_until
        await self.session.flush()
        return events

    async def mark_published(self, event: OutboxEvent, *, now: datetime | None = None) -> None:
        event.status = "published"
        event.published_at = now or datetime.now(UTC)
        event.locked_by = None
        event.locked_until = None
        event.last_error = None
        await self.session.flush()

    async def mark_failed(
        self,
        event: OutboxEvent,
        error: BaseException,
        *,
        retry_delay_seconds: int = 30,
        now: datetime | None = None,
    ) -> None:
        resolved_now = now or datetime.now(UTC)
        event.attempt_count += 1
        event.last_error = f"{type(error).__name__}: {error}"
        event.locked_by = None
        event.locked_until = None
        if event.attempt_count >= event.max_attempts:
            event.status = "dead_letter"
            event.dead_letter_reason = event.last_error
            event.next_retry_at = None
        else:
            event.status = "failed"
            event.next_retry_at = resolved_now + timedelta(seconds=retry_delay_seconds)
        await self.session.flush()

    async def replay_dead_letter(self, event: OutboxEvent) -> None:
        if event.status != "dead_letter":
            raise AppError("CONFLICT", "Only dead-letter events can be replayed", status_code=409)
        event.status = "pending"
        event.dead_letter_reason = None
        event.last_error = None
        event.next_retry_at = None
        event.locked_by = None
        event.locked_until = None
        await self.session.flush()

    def to_envelope(self, event: OutboxEvent) -> EventEnvelope:
        return EventEnvelope(
            event_id=event.id,
            event_type=event.event_type,
            event_version=event.event_version,
            tenant_id=event.tenant_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            payload=event.payload,
        )

    def _eligible_statement(self, now: datetime) -> Select[tuple[OutboxEvent]]:
        unlocked = or_(OutboxEvent.locked_until.is_(None), OutboxEvent.locked_until <= now)
        retryable = and_(
            OutboxEvent.status.in_(["pending", "failed"]),
            or_(OutboxEvent.next_retry_at.is_(None), OutboxEvent.next_retry_at <= now),
            unlocked,
        )
        abandoned = and_(OutboxEvent.status == "publishing", OutboxEvent.locked_until <= now)
        return select(OutboxEvent).where(or_(retryable, abandoned))

    def _validate_event(
        self,
        event_type: str,
        event_version: int,
        payload: dict[str, Any],
        tenant_id: str,
    ) -> None:
        if self.registry and not self.registry.has_event_type(event_type, event_version):
            raise AppError(
                "VALIDATION_ERROR",
                f"Unregistered event type: {event_type} v{event_version}",
                status_code=400,
            )
        missing_fields = {"tenant_id", "actor_id", "request_id"} - set(payload)
        if missing_fields:
            raise AppError(
                "VALIDATION_ERROR",
                f"Event payload missing required fields: {sorted(missing_fields)}",
                status_code=400,
            )
        if payload["tenant_id"] != tenant_id:
            raise AppError(
                "TENANT_CONTEXT_CONFLICT",
                "Event payload tenant_id must match outbox tenant_id",
                status_code=403,
            )
