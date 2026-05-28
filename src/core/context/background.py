from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from core.context.context import RequestContext, reset_current_context, set_current_context


@dataclass(frozen=True, slots=True)
class BackgroundContext:
    request_id: str
    route: str
    method: str
    tenant_id: str | None = None
    user_id: str | None = None
    trace_id: str | None = None

    def to_request_context(self) -> RequestContext:
        return RequestContext(
            request_id=self.request_id,
            trace_id=self.trace_id,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            route=self.route,
            method=self.method,
        ).freeze()


@contextmanager
def use_background_context(context: BackgroundContext) -> Iterator[RequestContext]:
    request_context = context.to_request_context()
    token = set_current_context(request_context)
    try:
        yield request_context
    finally:
        reset_current_context(token)


def task_background_context(
    *,
    task_id: str,
    task_type: str,
    tenant_id: str,
    request_id: str,
) -> BackgroundContext:
    return BackgroundContext(
        request_id=request_id or f"task:{task_id}",
        tenant_id=tenant_id,
        route=f"task:{task_type}",
        method="TASK",
    )


def outbox_background_context(
    *,
    event_id: str,
    event_type: str,
    event_version: int,
    tenant_id: str,
    payload: dict[str, Any],
) -> BackgroundContext:
    return BackgroundContext(
        request_id=str(payload.get("request_id") or f"outbox:{event_id}"),
        tenant_id=tenant_id,
        user_id=_optional_str(payload.get("actor_id")),
        route=f"outbox:{event_type}:v{event_version}",
        method="OUTBOX",
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
