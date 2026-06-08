from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.context.context import RequestContext, reset_current_context, set_current_context


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid4().hex}"
        context = RequestContext(
            request_id=request_id,
            trace_id=request.headers.get("X-Trace-ID") or request.headers.get("traceparent"),
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            route=request.url.path,
            method=request.method,
            started_at=datetime.now(UTC),
        )
        token = set_current_context(context)
        access_token = _set_empty_access_context()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _reset_access_context(access_token)
            reset_current_context(token)


def _set_empty_access_context():
    from core.permissions.context import set_current_access

    return set_current_access(None)


def _reset_access_context(token) -> None:  # type: ignore[no-untyped-def]
    from core.permissions.context import reset_current_access

    reset_current_access(token)
