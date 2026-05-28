from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core.cache.provider import CacheProvider
from core.exceptions import AppError


@dataclass(slots=True)
class _CacheEntry:
    value: Any
    expires_at: datetime | None


class MemoryCacheProvider(CacheProvider):
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._entries: dict[str, _CacheEntry] = {}

    async def get(self, key: str) -> Any | None:
        self._validate_key(key)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._expired(entry):
            self._entries.pop(key, None)
            return None
        return entry.value

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: int | None = None,
        permanent: bool = False,
    ) -> None:
        self._validate_key(key)
        expires_at = self._expires_at(ttl_seconds=ttl_seconds, permanent=permanent)
        self._entries[key] = _CacheEntry(value=value, expires_at=expires_at)

    async def delete(self, key: str) -> bool:
        self._validate_key(key)
        await self.get(key)
        return self._entries.pop(key, None) is not None

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def incr(
        self,
        key: str,
        *,
        amount: int = 1,
        ttl_seconds: int | None = None,
        permanent: bool = False,
    ) -> int:
        self._validate_key(key)
        current = await self.get(key)
        if current is None:
            expires_at = self._expires_at(ttl_seconds=ttl_seconds, permanent=permanent)
            value = amount
        else:
            if not isinstance(current, int):
                raise AppError(
                    "CONFLICT",
                    "Cache value is not an integer",
                    status_code=409,
                )
            entry = self._entries[key]
            expires_at = entry.expires_at
            value = current + amount
        self._entries[key] = _CacheEntry(value=value, expires_at=expires_at)
        return value

    async def expire(self, key: str, *, ttl_seconds: int) -> bool:
        self._validate_key(key)
        self._validate_ttl(ttl_seconds)
        entry = self._entries.get(key)
        if entry is None or self._expired(entry):
            self._entries.pop(key, None)
            return False
        entry.expires_at = self._now() + timedelta(seconds=ttl_seconds)
        return True

    async def get_json(self, key: str) -> Any | None:
        value = await self.get(key)
        if value is None:
            return None
        return json.loads(value)

    async def set_json(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: int | None = None,
        permanent: bool = False,
    ) -> None:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        await self.set(key, encoded, ttl_seconds=ttl_seconds, permanent=permanent)

    def _expires_at(self, *, ttl_seconds: int | None, permanent: bool) -> datetime | None:
        if permanent:
            if ttl_seconds is not None:
                raise AppError(
                    "VALIDATION_ERROR",
                    "Permanent cache entries cannot also declare TTL",
                    status_code=400,
                )
            return None
        if ttl_seconds is None:
            raise AppError(
                "VALIDATION_ERROR",
                "Cache entries require ttl_seconds unless permanent=True",
                status_code=400,
            )
        self._validate_ttl(ttl_seconds)
        return self._now() + timedelta(seconds=ttl_seconds)

    def _expired(self, entry: _CacheEntry) -> bool:
        return entry.expires_at is not None and entry.expires_at <= self._now()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _validate_key(self, key: str) -> None:
        if not key.strip():
            raise AppError("VALIDATION_ERROR", "Cache key must be non-empty", status_code=400)

    def _validate_ttl(self, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "Cache TTL must be greater than zero",
                status_code=400,
            )
