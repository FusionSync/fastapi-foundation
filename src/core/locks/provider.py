from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LockHandle:
    acquired: bool
    lock_key: str
    owner_token: str
    expires_at: datetime
    fencing_token: int


class LockProvider(Protocol):
    async def acquire(
        self,
        lock_key: str,
        *,
        ttl_seconds: int,
        owner_token: str | None = None,
    ) -> LockHandle: ...

    async def require_acquire(
        self,
        lock_key: str,
        *,
        ttl_seconds: int,
        owner_token: str | None = None,
    ) -> LockHandle: ...

    async def release(self, lock_key: str, *, owner_token: str) -> bool: ...

    async def extend(
        self,
        lock_key: str,
        *,
        owner_token: str,
        ttl_seconds: int,
    ) -> LockHandle | None: ...

    async def locked(self, lock_key: str) -> bool: ...
