from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from core.exceptions import AppError


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    text: str | None = None
    json_body: Any | None = None

    @property
    def body(self) -> Any | None:
        return self.json_body if self.json_body is not None else self.text


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str]
    json_body: Any | None
    timeout_seconds: float


class HttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: Any | None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class MockHttpTransport(HttpTransport):
    def __init__(self, *, responses: Sequence[HttpResponse | BaseException]) -> None:
        self._responses = list(responses)
        self.requests: list[HttpRequest] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: Any | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.requests.append(
            HttpRequest(
                method=method,
                url=url,
                headers=dict(headers),
                json_body=json_body,
                timeout_seconds=timeout_seconds,
            )
        )
        response = self.next_response()
        if isinstance(response, BaseException):
            raise response
        return response

    def next_response(self) -> HttpResponse | BaseException:
        if not self._responses:
            raise AppError(
                "VALIDATION_ERROR",
                "Mock HTTP transport has no scripted response",
                status_code=400,
            )
        return self._responses.pop(0)
