from __future__ import annotations

from typing import Any, Protocol


class CacheProvider(Protocol):
    async def get(self, key: str) -> Any | None: ...

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: int | None = None,
        permanent: bool = False,
    ) -> None: ...

    async def delete(self, key: str) -> bool: ...

    async def exists(self, key: str) -> bool: ...

    async def incr(
        self,
        key: str,
        *,
        amount: int = 1,
        ttl_seconds: int | None = None,
        permanent: bool = False,
    ) -> int: ...

    async def expire(self, key: str, *, ttl_seconds: int) -> bool: ...

    async def get_json(self, key: str) -> Any | None: ...

    async def set_json(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: int | None = None,
        permanent: bool = False,
    ) -> None: ...
