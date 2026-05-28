from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any

from core.context import get_current_context
from core.http_clients.config import HttpClientConfig
from core.http_clients.errors import ExternalServiceAppError
from core.http_clients.transport import HttpResponse, HttpTransport
from core.observability.metrics import MetricsRegistry
from core.security import redact_sensitive_data


class CoreHttpClient:
    def __init__(
        self,
        config: HttpClientConfig,
        *,
        transport: HttpTransport,
        metrics: MetricsRegistry | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.config = config
        self.transport = transport
        self.metrics = metrics
        self.clock = clock

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
        budget_started_at = self.clock()
        last_response: HttpResponse | None = None
        last_error: BaseException | None = None
        for _attempt in range(1, self.config.retry.max_attempts + 1):
            timeout_seconds = self._timeout_for_attempt(budget_started_at)
            if timeout_seconds <= 0:
                error = TimeoutError("HTTP timeout budget exceeded")
                self._record_metric(
                    resolved_method,
                    outcome="timeout_budget_exceeded",
                    error=error,
                )
                raise self._transport_error(resolved_method, url, json_body, error) from error
            try:
                response = await self.transport.request(
                    resolved_method,
                    url,
                    headers=request_headers,
                    json_body=json_body,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                if not self.config.retry.retry_exceptions:
                    self._record_metric(resolved_method, outcome="transport_error", error=exc)
                    raise self._transport_error(resolved_method, url, json_body, exc) from exc
                continue

            last_response = response
            if response.status_code < 400:
                self._record_metric(resolved_method, outcome="success", response=response)
                return response
            if response.status_code not in self.config.retry.retry_statuses:
                self._record_metric(resolved_method, outcome="http_error", response=response)
                raise self._response_error(resolved_method, url, json_body, response)

        if last_response is not None:
            self._record_metric(resolved_method, outcome="http_error", response=last_response)
            raise self._response_error(resolved_method, url, json_body, last_response)
        assert last_error is not None
        self._record_metric(resolved_method, outcome="transport_error", error=last_error)
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
                headers["traceparent"] = context.trace_id
        headers.update(explicit or {})
        return headers

    def _timeout_for_attempt(self, budget_started_at: float) -> float:
        if self.config.timeout_budget_seconds is None:
            return self.config.timeout_seconds
        elapsed_seconds = max(0.0, self.clock() - budget_started_at)
        remaining_seconds = self.config.timeout_budget_seconds - elapsed_seconds
        return min(self.config.timeout_seconds, remaining_seconds)

    def _record_metric(
        self,
        method: str,
        *,
        outcome: str,
        response: HttpResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        if self.metrics is None:
            return
        labels: dict[str, object] = {
            "service_name": self.config.service_name,
            "method": method,
            "outcome": outcome,
        }
        if response is not None:
            labels["status_class"] = f"{response.status_code // 100}xx"
        if error is not None:
            labels["error_type"] = type(error).__name__
        self.metrics.increment("external_http_requests_total", labels)

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
