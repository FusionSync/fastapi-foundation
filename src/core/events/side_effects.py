from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Literal

from core.exceptions import AppError
from core.idempotency import IdempotencyStore, hash_request_payload
from core.serialization import to_jsonable

from .registry import EventEnvelope

SideEffectOutcome = Literal["started", "replayed"]
SideEffectCallable = Callable[[], Awaitable[Any] | Any]


@dataclass(frozen=True, slots=True)
class EventSideEffectResult:
    outcome: SideEffectOutcome
    response_body: Any


@dataclass(frozen=True, slots=True)
class EventSideEffectContext:
    envelope: EventEnvelope
    handler_key: str
    idempotency_store: IdempotencyStore


_current_side_effect_context: ContextVar[EventSideEffectContext | None] = ContextVar(
    "current_event_side_effect_context",
    default=None,
)


@contextmanager
def use_event_side_effect_context(
    context: EventSideEffectContext | None,
) -> Any:
    token: Token[EventSideEffectContext | None] | None = None
    if context is not None:
        token = _current_side_effect_context.set(context)
    try:
        yield
    finally:
        if token is not None:
            _current_side_effect_context.reset(token)


async def run_event_side_effect(
    effect_key: str,
    effect: SideEffectCallable,
    *,
    request_payload: Mapping[str, Any] | None = None,
) -> EventSideEffectResult:
    context = _current_side_effect_context.get()
    if context is None:
        raise AppError(
            "SYSTEM_ERROR",
            "Event side effects require outbox dispatcher idempotency context",
            status_code=500,
            details={"effect_key": effect_key},
        )
    if not effect_key.strip():
        raise AppError(
            "VALIDATION_ERROR",
            "Event side effect key must be non-empty",
            status_code=400,
        )

    envelope = context.envelope
    claim = await context.idempotency_store.claim(
        tenant_id=envelope.tenant_id,
        user_id=str(envelope.payload.get("actor_id") or "__system__"),
        route=_side_effect_route(envelope, context.handler_key, effect_key),
        idempotency_key=envelope.event_id,
        request_hash=_side_effect_request_hash(
            envelope,
            handler_key=context.handler_key,
            effect_key=effect_key,
            request_payload=request_payload,
        ),
        retry_failed=True,
    )
    if claim.outcome == "replayed":
        return EventSideEffectResult(
            outcome="replayed",
            response_body=claim.record.response_body,
        )

    try:
        response_body = effect()
        if inspect.isawaitable(response_body):
            response_body = await response_body
        response_body = to_jsonable(response_body)
    except Exception as exc:
        await context.idempotency_store.mark_failed(
            claim.record,
            response_code=type(exc).__name__,
            response_body={"error": str(exc)},
        )
        raise

    await context.idempotency_store.mark_succeeded(
        claim.record,
        response_code="OK",
        response_body=response_body,
        outbox_event_id=envelope.event_id,
    )
    return EventSideEffectResult(outcome="started", response_body=response_body)


def _side_effect_route(
    envelope: EventEnvelope,
    handler_key: str,
    effect_key: str,
) -> str:
    return (
        f"outbox-side-effect:{envelope.event_type}:v{envelope.event_version}:"
        f"{handler_key}:{effect_key}"
    )


def _side_effect_request_hash(
    envelope: EventEnvelope,
    *,
    handler_key: str,
    effect_key: str,
    request_payload: Mapping[str, Any] | None,
) -> str:
    return hash_request_payload(
        {
            "event_id": envelope.event_id,
            "event_type": envelope.event_type,
            "event_version": envelope.event_version,
            "handler_key": handler_key,
            "effect_key": effect_key,
            "request_payload": dict(request_payload or {}),
        }
    )
