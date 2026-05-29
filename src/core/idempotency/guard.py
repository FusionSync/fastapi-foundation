from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from core.idempotency.keys import hash_request_payload
from core.idempotency.models import IdempotencyRecord
from core.idempotency.store import IdempotencyOutcome, IdempotencyStore

T = TypeVar("T")
MutationHandler = Callable[[], Awaitable[T] | T]
MutationResponseBuilder = Callable[[T], Any]
MutationSideEffectBuilder = Callable[[T], str | None]


@dataclass(frozen=True, slots=True)
class IdempotencyMutationResult(Generic[T]):
    outcome: IdempotencyOutcome
    record: IdempotencyRecord
    value: T | None
    response_code: str | None
    response_body: Any
    task_id: str | None = None
    outbox_event_id: str | None = None

    @property
    def replayed(self) -> bool:
        return self.outcome == "replayed"


class IdempotencyMutationGuard:
    def __init__(self, store: IdempotencyStore) -> None:
        self.store = store

    async def run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        route: str,
        idempotency_key: str,
        request_payload: Any,
        handler: MutationHandler[T],
        response_code: str,
        response_builder: MutationResponseBuilder[T],
        task_id_builder: MutationSideEffectBuilder[T] | None = None,
        outbox_event_id_builder: MutationSideEffectBuilder[T] | None = None,
        ttl_seconds: int = 24 * 60 * 60,
        lock_seconds: int = 60,
        retry_failed: bool = False,
    ) -> IdempotencyMutationResult[T]:
        claim = await self.store.claim(
            tenant_id=tenant_id,
            user_id=user_id,
            route=route,
            idempotency_key=idempotency_key,
            request_hash=hash_request_payload(request_payload),
            ttl_seconds=ttl_seconds,
            lock_seconds=lock_seconds,
            retry_failed=retry_failed,
        )
        if claim.outcome == "replayed":
            return IdempotencyMutationResult(
                outcome="replayed",
                record=claim.record,
                value=None,
                response_code=claim.record.response_code,
                response_body=claim.record.response_body,
                task_id=claim.record.task_id,
                outbox_event_id=claim.record.outbox_event_id,
            )

        try:
            value_or_awaitable = handler()
            if inspect.isawaitable(value_or_awaitable):
                value = await value_or_awaitable
            else:
                value = value_or_awaitable
            response_body = response_builder(value)
            task_id = task_id_builder(value) if task_id_builder is not None else None
            outbox_event_id = (
                outbox_event_id_builder(value) if outbox_event_id_builder is not None else None
            )
            await self.store.mark_succeeded(
                claim.record,
                response_code=response_code,
                response_body=response_body,
                task_id=task_id,
                outbox_event_id=outbox_event_id,
            )
            return IdempotencyMutationResult(
                outcome="started",
                record=claim.record,
                value=value,
                response_code=response_code,
                response_body=response_body,
                task_id=task_id,
                outbox_event_id=outbox_event_id,
            )
        except Exception as exc:
            await self.store.mark_failed(
                claim.record,
                response_code=type(exc).__name__,
                response_body={"error": str(exc)},
            )
            raise
