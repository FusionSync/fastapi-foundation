from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.events import EventEnvelope, EventRegistry
from core.events.dispatch_context import use_event_dispatch_session
from core.idempotency import IdempotencyStore
from core.locks import LockProvider
from core.observability import MetricsRegistry
from core.outbox.repository import OutboxRepository


@dataclass(frozen=True, slots=True)
class DispatchStats:
    claimed: int = 0
    published: int = 0
    failed: int = 0
    dead_lettered: int = 0


class OutboxExternalPublisher(Protocol):
    async def publish(self, envelope: EventEnvelope) -> None:
        raise NotImplementedError


class OutboxDispatcher:
    def __init__(
        self,
        repository: OutboxRepository,
        registry: EventRegistry,
        *,
        dispatcher_id: str,
        batch_size: int = 20,
        retry_delay_seconds: int = 30,
        metrics: MetricsRegistry | None = None,
        idempotency_store: IdempotencyStore | None = None,
        lock_provider: LockProvider | None = None,
        lock_key: str = "outbox:dispatch",
        lock_ttl_seconds: int = 60,
        external_publisher: OutboxExternalPublisher | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.dispatcher_id = dispatcher_id
        self.batch_size = batch_size
        self.retry_delay_seconds = retry_delay_seconds
        self.metrics = metrics
        self.idempotency_store = idempotency_store or IdempotencyStore(repository.session)
        self.lock_provider = lock_provider
        self.lock_key = lock_key
        self.lock_ttl_seconds = lock_ttl_seconds
        self.external_publisher = external_publisher

    async def dispatch_once(self) -> DispatchStats:
        lock_acquired = False
        if self.lock_provider is not None:
            handle = await self.lock_provider.acquire(
                self.lock_key,
                ttl_seconds=self.lock_ttl_seconds,
                owner_token=self.dispatcher_id,
            )
            if not handle.acquired:
                stats = DispatchStats()
                await self._record_metrics(stats)
                return stats
            lock_acquired = True
        try:
            return await self._dispatch_claimed()
        finally:
            if lock_acquired and self.lock_provider is not None:
                await self.lock_provider.release(self.lock_key, owner_token=self.dispatcher_id)

    async def _dispatch_claimed(self) -> DispatchStats:
        events = await self.repository.claim_batch(
            dispatcher_id=self.dispatcher_id,
            batch_size=self.batch_size,
        )
        published = 0
        failed = 0
        dead_lettered = 0
        for event in events:
            try:
                envelope = self.repository.to_envelope(event)
                if self.external_publisher is not None:
                    await self.external_publisher.publish(envelope)
                else:
                    with use_event_dispatch_session(self.repository.session):
                        await self.registry.dispatch(
                            envelope,
                            idempotency_store=self.idempotency_store,
                        )
            except Exception as exc:
                await self.repository.mark_failed(
                    event,
                    exc,
                    dispatcher_id=self.dispatcher_id,
                    retry_delay_seconds=self.retry_delay_seconds,
                )
                failed += 1
                if event.status == "dead_letter":
                    dead_lettered += 1
            else:
                await self.repository.mark_published(
                    event,
                    dispatcher_id=self.dispatcher_id,
                )
                published += 1
        stats = DispatchStats(
            claimed=len(events),
            published=published,
            failed=failed,
            dead_lettered=dead_lettered,
        )
        await self._record_metrics(stats)
        return stats

    async def _record_metrics(self, stats: DispatchStats) -> None:
        if self.metrics is None:
            return
        for outcome, value in {
            "claimed": stats.claimed,
            "published": stats.published,
            "failed": stats.failed,
            "dead_lettered": stats.dead_lettered,
        }.items():
            if value > 0:
                self.metrics.increment(
                    "outbox_dispatch_events_total",
                    {"outcome": outcome},
                    amount=value,
                )
        self.metrics.set_gauge(
            "outbox_events_pending",
            await self.repository.count_by_status("pending"),
        )
        self.metrics.set_gauge(
            "outbox_events_publishing",
            await self.repository.count_by_status("publishing"),
        )
        self.metrics.set_gauge(
            "outbox_events_dead_letter",
            await self.repository.count_by_status("dead_letter"),
        )
