from __future__ import annotations

from typing import Any

from core.context import get_current_context
from core.http_clients.config import HttpClientConfig
from core.http_clients.errors import ExternalServiceAppError
from core.http_clients.transport import HttpResponse, HttpTransport
from core.security import redact_sensitive_data


class CoreHttpClient:
    def __init__(
        self,
        config: HttpClientConfig,
        *,
        transport: HttpTransport,
    ) -> None:
        self.config = config
        self.transport = transport

    async def get(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        return await self.request("GET", path, headers=headers)

    async def post_json(
        self,
        path: str,
        *,
        json_body: Any,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        return await self.request("POST", path, json_body=json_body, headers=headers)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        resolved_method = method.upper()
        url = self.config.url_for(path)
        request_headers = self._headers(headers)
        last_response: HttpResponse | None = None
        last_error: BaseException | None = None
        for _attempt in range(1, self.config.retry.max_attempts + 1):
            try:
                response = await self.transport.request(
                    resolved_method,
                    url,
                    headers=request_headers,
                    json_body=json_body,
                    timeout_seconds=self.config.timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                if not self.config.retry.retry_exceptions:
                    raise self._transport_error(resolved_method, url, json_body, exc) from exc
                continue

            last_response = response
            if response.status_code < 400:
                return response
            if response.status_code not in self.config.retry.retry_statuses:
                raise self._response_error(resolved_method, url, json_body, response)

        if last_response is not None:
            raise self._response_error(resolved_method, url, json_body, last_response)
        assert last_error is not None
        raise self._transport_error(resolved_method, url, json_body, last_error) from last_error

    def _headers(self, explicit: dict[str, str] | None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.config.user_agent,
        }
        context = get_current_context()
        if context is not None:
            headers["X-Request-ID"] = context.request_id
            if context.trace_id:
                headers["X-Trace-ID"] = context.trace_id
        headers.update(explicit or {})
        return headers

    def _response_error(
        self,
        method: str,
        url: str,
        request_body: Any,
        response: HttpResponse,
    ) -> ExternalServiceAppError:
        return ExternalServiceAppError(
            service_name=self.config.service_name,
            message="External service returned an error",
            details={
                "service_name": self.config.service_name,
                "method": method,
                "url": url,
                "upstream_status_code": response.status_code,
                "request_id": self._request_id(),
                "request_body": redact_sensitive_data(request_body),
                "response_body": redact_sensitive_data(response.body),
            },
        )

    def _transport_error(
        self,
        method: str,
        url: str,
        request_body: Any,
        error: BaseException,
    ) -> ExternalServiceAppError:
        return ExternalServiceAppError(
            service_name=self.config.service_name,
            message="External service call failed",
            details={
                "service_name": self.config.service_name,
                "method": method,
                "url": url,
                "error_type": type(error).__name__,
                "request_id": self._request_id(),
                "request_body": redact_sensitive_data(request_body),
            },
        )

    def _request_id(self) -> str | None:
        context = get_current_context()
        return context.request_id if context is not None else None
