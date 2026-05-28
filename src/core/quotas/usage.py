from __future__ import annotations

from typing import Protocol

from core.exceptions import AppError


class QuotaUsageStore(Protocol):
    async def get_usage(self, key: str) -> int: ...

    async def set_usage(self, key: str, value: int) -> None: ...

    async def reserve(self, key: str, *, amount: int, limit: int) -> int | None: ...

    async def release(self, key: str, *, amount: int) -> int: ...


class MemoryQuotaUsageStore(QuotaUsageStore):
    def __init__(self) -> None:
        self._usage: dict[str, int] = {}

    async def get_usage(self, key: str) -> int:
        self._validate_key(key)
        return self._usage.get(key, 0)

    async def set_usage(self, key: str, value: int) -> None:
        self._validate_key(key)
        self._validate_non_negative(value, "Quota usage")
        self._usage[key] = value

    async def reserve(self, key: str, *, amount: int, limit: int) -> int | None:
        self._validate_key(key)
        self._validate_positive(amount, "Quota amount")
        self._validate_non_negative(limit, "Quota limit")
        current = self._usage.get(key, 0)
        projected = current + amount
        if projected > limit:
            return None
        self._usage[key] = projected
        return projected

    async def release(self, key: str, *, amount: int) -> int:
        self._validate_key(key)
        self._validate_positive(amount, "Quota amount")
        current = self._usage.get(key, 0)
        updated = max(current - amount, 0)
        self._usage[key] = updated
        return updated

    def _validate_key(self, key: str) -> None:
        if not key.strip():
            raise AppError("VALIDATION_ERROR", "Quota usage key is required", status_code=400)

    def _validate_positive(self, value: int, label: str) -> None:
        if value <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                f"{label} must be greater than zero",
                status_code=400,
            )

    def _validate_non_negative(self, value: int, label: str) -> None:
        if value < 0:
            raise AppError(
                "VALIDATION_ERROR",
                f"{label} must be greater than or equal to zero",
                status_code=400,
            )
