from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    trace_id: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    locale: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    route: str | None = None
    method: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    frozen: bool = False

    def with_user(self, user_id: str) -> RequestContext:
        self._ensure_mutable("user_id")
        return replace(self, user_id=user_id)

    def with_tenant(self, tenant_id: str) -> RequestContext:
        self._ensure_mutable("tenant_id")
        return replace(self, tenant_id=tenant_id)

    def freeze(self) -> RequestContext:
        return replace(self, frozen=True)

    def _ensure_mutable(self, field_name: str) -> None:
        if self.frozen:
            raise RuntimeError(f"RequestContext is frozen; cannot modify {field_name}")


_current_context: ContextVar[RequestContext | None] = ContextVar(
    "current_request_context",
    default=None,
)


def set_current_context(context: RequestContext) -> Token[RequestContext | None]:
    return _current_context.set(context)


def get_current_context() -> RequestContext | None:
    return _current_context.get()


def reset_current_context(token: Token[RequestContext | None]) -> None:
    _current_context.reset(token)
