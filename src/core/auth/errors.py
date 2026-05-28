from __future__ import annotations

from typing import NoReturn

from core.exceptions import AppError


def invalid_auth_token(reason: str) -> NoReturn:
    raise AppError(
        "AUTH_INVALID_TOKEN",
        "Invalid authentication token",
        status_code=401,
        details={"reason": reason},
        headers={"WWW-Authenticate": "Bearer"},
    )
