from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.quotas.models import QuotaUsage


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


class DatabaseQuotaUsageStore(QuotaUsageStore):
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_usage(self, key: str) -> int:
        self._validate_key(key)
        async with self._session_factory() as session:
            usage = await self._usage_row(session, key)
            return usage.used if usage is not None else 0

    async def set_usage(self, key: str, value: int) -> None:
        self._validate_key(key)
        self._validate_non_negative(value, "Quota usage")
        async with self._session_factory() as session:
            usage = await self._usage_row(session, key)
            if usage is None:
                session.add(QuotaUsage(usage_key=key, used=value))
            else:
                usage.used = value
            await session.commit()

    async def reserve(self, key: str, *, amount: int, limit: int) -> int | None:
        self._validate_key(key)
        self._validate_positive(amount, "Quota amount")
        self._validate_non_negative(limit, "Quota limit")
        if amount > limit:
            return None
        for _ in range(2):
            async with self._session_factory() as session:
                updated = await session.execute(
                    update(QuotaUsage)
                    .where(QuotaUsage.usage_key == key)
                    .where(QuotaUsage.used + amount <= limit)
                    .values(used=QuotaUsage.used + amount)
                    .execution_options(synchronize_session=False)
                )
                if updated.rowcount == 1:
                    await session.commit()
                    return await self.get_usage(key)

                usage = await self._usage_row(session, key)
                if usage is not None:
                    return None

                try:
                    session.add(QuotaUsage(usage_key=key, used=amount))
                    await session.commit()
                    return amount
                except IntegrityError:
                    await session.rollback()
        return None

    async def release(self, key: str, *, amount: int) -> int:
        self._validate_key(key)
        self._validate_positive(amount, "Quota amount")
        async with self._session_factory() as session:
            usage = await self._usage_row(session, key)
            if usage is None:
                return 0
            usage.used = max(usage.used - amount, 0)
            await session.commit()
            return usage.used

    async def _usage_row(
        self,
        session: AsyncSession,
        key: str,
    ) -> QuotaUsage | None:
        result = await session.execute(
            select(QuotaUsage).where(QuotaUsage.usage_key == key)
        )
        return result.scalar_one_or_none()

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
