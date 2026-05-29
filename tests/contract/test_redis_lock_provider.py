import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.exceptions import AppError
from core.locks import RedisLockProvider


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 5, 28, tzinfo=UTC)

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass(slots=True)
class _RedisEntry:
    value: str
    expires_at: datetime | None = None


class FakeRedis:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.values: dict[str, _RedisEntry] = {}

    async def incr(self, name: str) -> int:
        current = await self.get(name)
        next_value = int(current or 0) + 1
        self.values[name] = _RedisEntry(str(next_value))
        return next_value

    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> bool:
        self._purge_if_expired(name)
        if nx and name in self.values:
            return False
        expires_at = (
            self.clock.now + timedelta(milliseconds=px)
            if px is not None
            else None
        )
        self.values[name] = _RedisEntry(value, expires_at=expires_at)
        return True

    async def get(self, name: str) -> str | None:
        self._purge_if_expired(name)
        entry = self.values.get(name)
        return entry.value if entry else None

    async def pttl(self, name: str) -> int:
        self._purge_if_expired(name)
        entry = self.values.get(name)
        if entry is None:
            return -2
        if entry.expires_at is None:
            return -1
        delta = entry.expires_at - self.clock.now
        return max(int(delta.total_seconds() * 1000), 0)

    async def eval(self, script: str, _numkeys: int, *args: Any) -> Any:
        if "redis-lock-release" in script:
            lock_key, owner_token = str(args[0]), str(args[1])
            payload = await self.get(lock_key)
            if payload is None or json.loads(payload)["owner_token"] != owner_token:
                return 0
            self.values.pop(lock_key, None)
            return 1
        if "redis-lock-extend" in script:
            lock_key, owner_token, ttl_ms = str(args[0]), str(args[1]), int(args[2])
            payload = await self.get(lock_key)
            if payload is None or json.loads(payload)["owner_token"] != owner_token:
                return [0, payload, await self.pttl(lock_key)]
            self.values[lock_key] = _RedisEntry(
                payload,
                expires_at=self.clock.now + timedelta(milliseconds=ttl_ms),
            )
            return [1, payload, ttl_ms]
        raise AssertionError(f"Unknown Redis script: {script}")

    def _purge_if_expired(self, name: str) -> None:
        entry = self.values.get(name)
        if (
            entry is not None
            and entry.expires_at is not None
            and entry.expires_at <= self.clock.now
        ):
            self.values.pop(name, None)


@pytest.mark.asyncio
async def test_redis_lock_acquire_release_and_owner_validation() -> None:
    clock = Clock()
    locks = RedisLockProvider(FakeRedis(clock), clock=lambda: clock.now)

    acquired = await locks.acquire(
        "schedule:daily-close",
        owner_token="owner-1",
        ttl_seconds=30,
    )
    blocked = await locks.acquire(
        "schedule:daily-close",
        owner_token="owner-2",
        ttl_seconds=30,
    )

    assert acquired.acquired is True
    assert acquired.lock_key == "schedule:daily-close"
    assert acquired.owner_token == "owner-1"
    assert acquired.expires_at == clock.now + timedelta(seconds=30)
    assert acquired.fencing_token == 1
    assert blocked.acquired is False
    assert blocked.fencing_token == 1
    assert await locks.locked("schedule:daily-close") is True
    assert await locks.release("schedule:daily-close", owner_token="owner-2") is False
    assert await locks.release("schedule:daily-close", owner_token="owner-1") is True
    assert await locks.locked("schedule:daily-close") is False


@pytest.mark.asyncio
async def test_redis_lock_extend_and_reacquire_after_expiry() -> None:
    clock = Clock()
    locks = RedisLockProvider(FakeRedis(clock), clock=lambda: clock.now)

    acquired = await locks.acquire(
        "file:file-1:process",
        owner_token="owner-1",
        ttl_seconds=30,
    )
    wrong_owner = await locks.extend(
        "file:file-1:process",
        owner_token="owner-2",
        ttl_seconds=60,
    )
    extended = await locks.extend(
        "file:file-1:process",
        owner_token="owner-1",
        ttl_seconds=60,
    )
    clock.advance(61)
    expired = await locks.locked("file:file-1:process")
    reacquired = await locks.acquire(
        "file:file-1:process",
        owner_token="owner-2",
        ttl_seconds=30,
    )

    assert acquired.fencing_token == 1
    assert wrong_owner is None
    assert extended is not None
    assert extended.expires_at == datetime(2026, 5, 28, 0, 1, tzinfo=UTC)
    assert expired is False
    assert reacquired.acquired is True
    assert reacquired.fencing_token > acquired.fencing_token


@pytest.mark.asyncio
async def test_redis_lock_require_acquire_raises_stable_code_when_locked() -> None:
    locks = RedisLockProvider(FakeRedis(Clock()))
    await locks.acquire("scheduler:hourly", owner_token="owner-1", ttl_seconds=60)

    with pytest.raises(AppError) as locked:
        await locks.require_acquire(
            "scheduler:hourly",
            owner_token="owner-2",
            ttl_seconds=60,
        )

    assert locked.value.code == "LOCK_NOT_ACQUIRED"
    assert locked.value.details == {"lock_key": "scheduler:hourly"}
