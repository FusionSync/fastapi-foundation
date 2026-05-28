from __future__ import annotations

from time import monotonic

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.context import get_current_context
from core.observability.logging import log_http_request
from core.observability.metrics import MetricsRegistry


class HttpRequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        started_at = monotonic()
        status_code = 500
        app_code = "SYSTEM_ERROR"
        try:
            response = await call_next(request)
            status_code = response.status_code
            app_code = _response_app_code(response)
            return response
        finally:
            log_http_request(
                context=get_current_context(),
                status_code=status_code,
                app_code=app_code,
                duration_ms=round((monotonic() - started_at) * 1000, 3),
                settings=getattr(request.app.state, "settings", None),
            )


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        try:
            response = await call_next(request)
        except Exception:
            self._record_request(request, status_code=500)
            raise

        self._record_request(request, status_code=response.status_code)
        return response

    def _record_request(self, request: Request, *, status_code: int) -> None:
        registry = getattr(request.app.state, "metrics_registry", None)
        if not isinstance(registry, MetricsRegistry):
            return

        registry.increment(
            "http_requests_total",
            {
                "method": request.method.upper(),
                "route": _route_path(request),
                "status_class": f"{status_code // 100}xx",
            },
        )


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", None) or request.url.path)


def _response_app_code(response: Response) -> str:
    header_code = response.headers.get("X-App-Code")
    if header_code:
        return header_code
    if response.status_code < 400:
        return "OK"
    return "UNKNOWN_ERROR"
