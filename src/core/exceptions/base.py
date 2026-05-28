from typing import Any


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.message_provided = message is not None
        self.status_code = status_code
        self.details = details
        self.headers = headers or {}
