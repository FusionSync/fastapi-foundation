from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from core.exceptions import AppError
from core.locks.provider import LockHandle, LockProvider


@dataclass(slots=True)
class _LockEntry:
    owner_token: str
    expires_at: datetime
    fencing_token: int


class MemoryLockProvider(LockProvider):
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._locks: dict[str, _LockEntry] = {}
        self._fencing_tokens: dict[str, int] = {}

    async def acquire(
        self,
        lock_key: str,
        *,
        ttl_seconds: int,
        owner_token: str | None = None,
    ) -> LockHandle:
        self._validate(lock_key=lock_key, ttl_seconds=ttl_seconds, owner_token=owner_token)
        resolved_owner = owner_token or str(uuid4())
        self._purge_if_expired(lock_key)
        existing = self._locks.get(lock_key)
        if existing is not None:
            return LockHandle(
                acquired=False,
                lock_key=lock_key,
                owner_token=resolved_owner,
                expires_at=existing.expires_at,
                fencing_token=existing.fencing_token,
            )

        fencing_token = self._fencing_tokens.get(lock_key, 0) + 1
        self._fencing_tokens[lock_key] = fencing_token
        expires_at = self._now() + timedelta(seconds=ttl_seconds)
        self._locks[lock_key] = _LockEntry(
            owner_token=resolved_owner,
            expires_at=expires_at,
            fencing_token=fencing_token,
        )
        return LockHandle(
            acquired=True,
            lock_key=lock_key,
            owner_token=resolved_owner,
            expires_at=expires_at,
            fencing_token=fencing_token,
        )

    async def require_acquire(
        self,
        lock_key: str,
        *,
        ttl_seconds: int,
        owner_token: str | None = None,
    ) -> LockHandle:
        handle = await self.acquire(
            lock_key,
            ttl_seconds=ttl_seconds,
            owner_token=owner_token,
        )
        if handle.acquired:
            return handle
        raise AppError(
            "LOCK_NOT_ACQUIRED",
            "Lock is already held",
            status_code=409,
            details={"lock_key": lock_key},
        )

    async def release(self, lock_key: str, *, owner_token: str) -> bool:
        self._validate_key(lock_key)
        self._validate_owner(owner_token)
        self._purge_if_expired(lock_key)
        existing = self._locks.get(lock_key)
        if existing is None or existing.owner_token != owner_token:
            return False
        self._locks.pop(lock_key, None)
        return True

    async def extend(
        self,
        lock_key: str,
        *,
        owner_token: str,
        ttl_seconds: int,
    ) -> LockHandle | None:
        self._validate(lock_key=lock_key, ttl_seconds=ttl_seconds, owner_token=owner_token)
        self._purge_if_expired(lock_key)
        existing = self._locks.get(lock_key)
        if existing is None or existing.owner_token != owner_token:
            return None
        existing.expires_at = self._now() + timedelta(seconds=ttl_seconds)
        return LockHandle(
            acquired=True,
            lock_key=lock_key,
            owner_token=owner_token,
            expires_at=existing.expires_at,
            fencing_token=existing.fencing_token,
        )

    async def locked(self, lock_key: str) -> bool:
        self._validate_key(lock_key)
        self._purge_if_expired(lock_key)
        return lock_key in self._locks

    def _purge_if_expired(self, lock_key: str) -> None:
        existing = self._locks.get(lock_key)
        if existing is not None and existing.expires_at <= self._now():
            self._locks.pop(lock_key, None)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _validate(
        self,
        *,
        lock_key: str,
        ttl_seconds: int,
        owner_token: str | None,
    ) -> None:
        self._validate_key(lock_key)
        if owner_token is not None:
            self._validate_owner(owner_token)
        if ttl_seconds <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "Lock TTL must be greater than zero",
                status_code=400,
            )

    def _validate_key(self, lock_key: str) -> None:
        if not lock_key.strip():
            raise AppError("VALIDATION_ERROR", "Lock key must be non-empty", status_code=400)

    def _validate_owner(self, owner_token: str) -> None:
        if not owner_token.strip():
            raise AppError("VALIDATION_ERROR", "Owner token must be non-empty", status_code=400)
