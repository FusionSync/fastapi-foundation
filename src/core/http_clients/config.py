from __future__ import annotations

from dataclasses import dataclass, field

from core.exceptions import AppError


@dataclass(frozen=True, slots=True)
class RetryConfig:
    max_attempts: int = 1
    retry_statuses: tuple[int, ...] = (502, 503, 504)
    retry_exceptions: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise AppError(
                "VALIDATION_ERROR",
                "HTTP retry max_attempts must be at least one",
                status_code=400,
            )
        invalid_statuses = [status for status in self.retry_statuses if status < 400]
        if invalid_statuses:
            raise AppError(
                "VALIDATION_ERROR",
                "HTTP retry statuses must be error statuses",
                status_code=400,
            )


@dataclass(frozen=True, slots=True)
class HttpClientConfig:
    service_name: str
    base_url: str
    timeout_seconds: float = 5.0
    timeout_budget_seconds: float | None = None
    retry: RetryConfig = field(default_factory=RetryConfig)
    user_agent: str = "service-core/0.1"

    def __post_init__(self) -> None:
        if not self.service_name.strip():
            raise AppError("VALIDATION_ERROR", "HTTP service_name is required", status_code=400)
        if not self.base_url.strip():
            raise AppError("VALIDATION_ERROR", "HTTP base_url is required", status_code=400)
        if self.timeout_seconds <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "HTTP timeout_seconds must be greater than zero",
                status_code=400,
            )
        if self.timeout_budget_seconds is not None and self.timeout_budget_seconds <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "HTTP timeout_budget_seconds must be greater than zero",
                status_code=400,
            )
        if not self.user_agent.strip():
            raise AppError("VALIDATION_ERROR", "HTTP user_agent is required", status_code=400)

    def url_for(self, path: str) -> str:
        if not path.strip():
            raise AppError("VALIDATION_ERROR", "HTTP request path is required", status_code=400)
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
