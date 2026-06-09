from __future__ import annotations

from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.context import get_current_context
from core.exceptions import AppError
from core.rate_limit.provider import CacheRateLimiter, RateLimitDecision
from core.rate_limit.rules import RateLimitIdentity, RateLimitRegistry
from core.serialization import fail


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        registry = getattr(request.app.state, "rate_limit_registry", None)
        limiter = getattr(request.app.state, "rate_limiter", None)
        if not isinstance(registry, RateLimitRegistry) or not isinstance(
            limiter,
            CacheRateLimiter,
        ):
            return await call_next(request)

        route = _route_key(request)
        rule = registry.find(route)
        if rule is None:
            return await call_next(request)

        try:
            decision = await limiter.check(rule, _identity_for(request, route))
        except AppError as exc:
            if exc.code == "VALIDATION_ERROR":
                return await call_next(request)
            raise
        if decision.allowed:
            return await call_next(request)
        return _rate_limited_response(request, decision)


def _identity_for(request: Request, route: str) -> RateLimitIdentity:
    context = get_current_context()
    return RateLimitIdentity(
        tenant_id=context.tenant_id if context else None,
        user_id=context.user_id if context else None,
        ip_address=request.client.host if request.client else None,
        route=route,
    )


def _route_key(request: Request) -> str:
    return f"{request.method} {request.url.path}"


def _rate_limited_response(
    request: Request,
    decision: RateLimitDecision,
) -> JSONResponse:
    request_id = _request_id(request)
    settings = getattr(request.app.state, "settings", None)
    status_code = (
        200
        if settings is not None and settings.api.error_http_status_mode == "always_200"
        else 429
    )
    headers = {
        "X-App-Code": "RATE_LIMITED",
        "X-Request-ID": request_id,
        **decision.headers,
    }
    return JSONResponse(
        fail(
            "RATE_LIMITED",
            details=decision.details(),
            request_id=request_id,
        ),
        status_code=status_code,
        headers=headers,
    )


def _request_id(request: Request) -> str:
    context = get_current_context()
    if context is not None:
        return context.request_id
    return request.headers.get("X-Request-ID") or "req_unknown"
