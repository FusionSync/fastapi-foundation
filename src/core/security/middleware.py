from __future__ import annotations

from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.security.headers import SecurityHeadersConfig, security_headers


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Callable,
        config: SecurityHeadersConfig | None = None,
    ) -> None:
        super().__init__(app)
        self.headers = security_headers(config)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        for name, value in self.headers.items():
            response.headers.setdefault(name, value)
        return response


class RequestBodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Callable, max_body_bytes: int | None) -> None:
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self.max_body_bytes is not None:
            content_length = request.headers.get("content-length")
            if content_length is not None and int(content_length) > self.max_body_bytes:
                return _security_error_response(
                    "REQUEST_TOO_LARGE",
                    status_code=413,
                    details={
                        "max_bytes": self.max_body_bytes,
                        "content_length": int(content_length),
                    },
                )
        return await call_next(request)


class TrustedHostGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Callable, allowed_hosts: list[str]) -> None:
        super().__init__(app)
        self.allowed_hosts = [host.lower() for host in allowed_hosts]

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self.allowed_hosts and "*" not in self.allowed_hosts:
            host = (request.headers.get("host") or "").split(":", maxsplit=1)[0].lower()
            if not _is_allowed_host(host, self.allowed_hosts):
                return _security_error_response(
                    "HOST_NOT_ALLOWED",
                    status_code=400,
                    details={"host": host},
                )
        return await call_next(request)


def _is_allowed_host(host: str, allowed_hosts: list[str]) -> bool:
    for allowed in allowed_hosts:
        if allowed == host:
            return True
        if allowed.startswith("*.") and host.endswith(allowed[1:]):
            return True
    return False


def _security_error_response(
    code: str,
    *,
    status_code: int,
    details: dict[str, object],
) -> JSONResponse:
    from core.serialization import fail

    return JSONResponse(
        fail(code, details=details),
        status_code=status_code,
        headers={"X-App-Code": code},
    )
