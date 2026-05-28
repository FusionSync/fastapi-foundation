from __future__ import annotations

from dataclasses import dataclass

from core.events import EventRegistry
from core.observability import MetricsRegistry
from core.outbox.repository import OutboxRepository


@dataclass(frozen=True, slots=True)
class DispatchStats:
    claimed: int = 0
    published: int = 0
    failed: int = 0
    dead_lettered: int = 0


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
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.dispatcher_id = dispatcher_id
        self.batch_size = batch_size
        self.retry_delay_seconds = retry_delay_seconds
        self.metrics = metrics

    async def dispatch_once(self) -> DispatchStats:
        events = await self.repository.claim_batch(
            dispatcher_id=self.dispatcher_id,
            batch_size=self.batch_size,
        )
        published = 0
        failed = 0
        dead_lettered = 0
        for event in events:
            try:
                await self.registry.dispatch(self.repository.to_envelope(event))
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
