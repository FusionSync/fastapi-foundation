from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.locks.models import DatabaseLock
from core.locks.provider import LockHandle, LockProvider


class DatabaseLockProvider(LockProvider):
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def acquire(
        self,
        lock_key: str,
        *,
        ttl_seconds: int,
        owner_token: str | None = None,
    ) -> LockHandle:
        self._validate(lock_key=lock_key, ttl_seconds=ttl_seconds, owner_token=owner_token)
        resolved_owner = owner_token or str(uuid4())
        for _ in range(2):
            async with self._session_factory() as session:
                try:
                    handle = await self._acquire_in_session(
                        session,
                        lock_key=lock_key,
                        ttl_seconds=ttl_seconds,
                        owner_token=resolved_owner,
                    )
                    if handle.acquired:
                        await session.commit()
                    return handle
                except IntegrityError:
                    await session.rollback()
        async with self._session_factory() as session:
            handle = await self._read_locked_handle(
                session,
                lock_key=lock_key,
                owner_token=resolved_owner,
            )
            if handle is not None:
                return handle
        raise AppError(
            "LOCK_NOT_ACQUIRED",
            "Lock could not be acquired after retry",
            status_code=409,
            details={"lock_key": lock_key},
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
        async with self._session_factory() as session:
            existing = await self._lock_row(session, lock_key)
            now = self._now()
            if (
                existing is None
                or existing.owner_token != owner_token
                or self._expires_at(existing.expires_at) <= now
            ):
                return False
            result = await session.execute(
                update(DatabaseLock)
                .where(DatabaseLock.lock_key == lock_key)
                .where(DatabaseLock.owner_token == owner_token)
                .where(DatabaseLock.expires_at > now)
                .values(expires_at=now)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                return False
            await session.commit()
            return True

    async def extend(
        self,
        lock_key: str,
        *,
        owner_token: str,
        ttl_seconds: int,
    ) -> LockHandle | None:
        self._validate(lock_key=lock_key, ttl_seconds=ttl_seconds, owner_token=owner_token)
        async with self._session_factory() as session:
            existing = await self._lock_row(session, lock_key)
            now = self._now()
            if (
                existing is None
                or existing.owner_token != owner_token
                or self._expires_at(existing.expires_at) <= now
            ):
                return None
            expires_at = now + timedelta(seconds=ttl_seconds)
            result = await session.execute(
                update(DatabaseLock)
                .where(DatabaseLock.lock_key == lock_key)
                .where(DatabaseLock.owner_token == owner_token)
                .where(DatabaseLock.expires_at > now)
                .values(expires_at=expires_at)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                return None
            await session.refresh(existing)
            await session.commit()
            return LockHandle(
                acquired=True,
                lock_key=lock_key,
                owner_token=owner_token,
                expires_at=expires_at,
                fencing_token=existing.fencing_token,
            )

    async def locked(self, lock_key: str) -> bool:
        self._validate_key(lock_key)
        async with self._session_factory() as session:
            existing = await self._lock_row(session, lock_key)
            return existing is not None and self._expires_at(existing.expires_at) > self._now()

    async def _acquire_in_session(
        self,
        session: AsyncSession,
        *,
        lock_key: str,
        ttl_seconds: int,
        owner_token: str,
    ) -> LockHandle:
        now = self._now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        existing = await self._lock_row(session, lock_key)
        if existing is not None and self._expires_at(existing.expires_at) > now:
            return LockHandle(
                acquired=False,
                lock_key=lock_key,
                owner_token=owner_token,
                expires_at=self._expires_at(existing.expires_at),
                fencing_token=existing.fencing_token,
            )
        if existing is None:
            existing = DatabaseLock(
                lock_key=lock_key,
                owner_token=owner_token,
                expires_at=expires_at,
                fencing_token=1,
            )
            session.add(existing)
        else:
            result = await session.execute(
                update(DatabaseLock)
                .where(DatabaseLock.lock_key == lock_key)
                .where(DatabaseLock.expires_at <= now)
                .values(
                    owner_token=owner_token,
                    expires_at=expires_at,
                    fencing_token=DatabaseLock.fencing_token + 1,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                blocked = await self._read_locked_handle(
                    session,
                    lock_key=lock_key,
                    owner_token=owner_token,
                )
                if blocked is not None:
                    return blocked
                raise AppError(
                    "LOCK_NOT_ACQUIRED",
                    "Lock changed while acquiring",
                    status_code=409,
                    details={"lock_key": lock_key},
                )
            await session.refresh(existing)
        await session.flush()
        return LockHandle(
            acquired=True,
            lock_key=lock_key,
            owner_token=owner_token,
            expires_at=expires_at,
            fencing_token=existing.fencing_token,
        )

    async def _read_locked_handle(
        self,
        session: AsyncSession,
        *,
        lock_key: str,
        owner_token: str,
    ) -> LockHandle | None:
        existing = await self._lock_row(session, lock_key)
        if existing is None:
            return None
        return LockHandle(
            acquired=False,
            lock_key=lock_key,
            owner_token=owner_token,
            expires_at=self._expires_at(existing.expires_at),
            fencing_token=existing.fencing_token,
        )

    async def _lock_row(self, session: AsyncSession, lock_key: str) -> DatabaseLock | None:
        result = await session.execute(
            select(DatabaseLock).where(DatabaseLock.lock_key == lock_key)
        )
        return result.scalar_one_or_none()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _expires_at(self, value: datetime) -> datetime:
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
