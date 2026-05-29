from __future__ import annotations

import json
from typing import Any, Protocol

from core.cache.provider import CacheProvider
from core.exceptions import AppError


class RedisCacheClient(Protocol):
    async def get(self, name: str) -> Any: ...

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
    ) -> Any: ...

    async def delete(self, name: str) -> int: ...

    async def exists(self, name: str) -> int: ...

    async def incrby(self, name: str, amount: int) -> int: ...

    async def expire(self, name: str, seconds: int) -> Any: ...


class RedisCacheProvider(CacheProvider):
    def __init__(self, client: RedisCacheClient) -> None:
        self._client = client

    async def get(self, key: str) -> Any | None:
        self._validate_key(key)
        value = await self._client.get(key)
        if value is None:
            return None
        return self._decode_value(value)

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: int | None = None,
        permanent: bool = False,
    ) -> None:
        self._validate_key(key)
        ex = self._expiration_seconds(ttl_seconds=ttl_seconds, permanent=permanent)
        await self._client.set(key, self._encode_value(value), ex=ex)

    async def delete(self, key: str) -> bool:
        self._validate_key(key)
        return bool(await self._client.delete(key))

    async def exists(self, key: str) -> bool:
        self._validate_key(key)
        return bool(await self._client.exists(key))

    async def incr(
        self,
        key: str,
        *,
        amount: int = 1,
        ttl_seconds: int | None = None,
        permanent: bool = False,
    ) -> int:
        self._validate_key(key)
        current = await self._client.get(key)
        if current is None:
            ex = self._expiration_seconds(
                ttl_seconds=ttl_seconds,
                permanent=permanent,
            )
            await self._client.set(key, str(amount), ex=ex)
            return amount
        try:
            int(self._decode_value(current))
        except ValueError as exc:
            raise AppError(
                "CONFLICT",
                "Cache value is not an integer",
                status_code=409,
            ) from exc
        return int(await self._client.incrby(key, amount))

    async def expire(self, key: str, *, ttl_seconds: int) -> bool:
        self._validate_key(key)
        self._validate_ttl(ttl_seconds)
        return bool(await self._client.expire(key, ttl_seconds))

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

    def _expiration_seconds(
        self,
        *,
        ttl_seconds: int | None,
        permanent: bool,
    ) -> int | None:
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
        return ttl_seconds

    def _encode_value(self, value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def _decode_value(self, value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

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
