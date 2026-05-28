from __future__ import annotations

from dataclasses import dataclass

from core.exceptions.base import AppError
from core.security import DEFAULT_SENSITIVE_KEYS


@dataclass(frozen=True, slots=True)
class MessageCatalog:
    locale: str
    owner_module: str
    messages: dict[str, str]

    def __post_init__(self) -> None:
        if not self.locale.strip():
            raise AppError("VALIDATION_ERROR", "message locale is required", status_code=400)
        if not self.owner_module.strip():
            raise AppError("VALIDATION_ERROR", "message owner_module is required", status_code=400)
        if not self.messages:
            raise AppError("VALIDATION_ERROR", "message catalog must not be empty", status_code=400)
        for code, message in self.messages.items():
            if not code.strip():
                raise AppError("VALIDATION_ERROR", "message code is required", status_code=400)
            if not message.strip():
                raise AppError("VALIDATION_ERROR", "message text is required", status_code=400)
            if _contains_sensitive_word(message):
                raise AppError(
                    "VALIDATION_ERROR",
                    "message text must not contain sensitive words",
                    status_code=400,
                    details={"code": code, "reason": "sensitive_message"},
                )


def _contains_sensitive_word(message: str) -> bool:
    lowered = message.lower()
    return any(word in lowered for word in DEFAULT_SENSITIVE_KEYS)
