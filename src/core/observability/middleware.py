from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.observability.metrics import MetricsRegistry


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
