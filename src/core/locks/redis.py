from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from core.exceptions import AppError
from core.locks.provider import LockHandle, LockProvider

_RELEASE_SCRIPT = """
-- redis-lock-release
local value = redis.call("get", KEYS[1])
if not value then
  return 0
end
local payload = cjson.decode(value)
if payload["owner_token"] ~= ARGV[1] then
  return 0
end
return redis.call("del", KEYS[1])
"""

_EXTEND_SCRIPT = """
-- redis-lock-extend
local value = redis.call("get", KEYS[1])
if not value then
  return {0, false, -2}
end
local payload = cjson.decode(value)
if payload["owner_token"] ~= ARGV[1] then
  return {0, value, redis.call("pttl", KEYS[1])}
end
redis.call("pexpire", KEYS[1], ARGV[2])
return {1, value, redis.call("pttl", KEYS[1])}
"""


class RedisLockClient(Protocol):
    async def incr(self, name: str) -> int: ...

    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> Any: ...

    async def get(self, name: str) -> Any: ...

    async def pttl(self, name: str) -> int: ...

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any: ...


class RedisLockProvider(LockProvider):
    def __init__(
        self,
        client: RedisLockClient,
        *,
        prefix: str = "core:locks",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._prefix = prefix.rstrip(":")
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
        redis_key = self._lock_key(lock_key)
        ttl_ms = ttl_seconds * 1000
        for _ in range(2):
            fencing_token = int(await self._client.incr(self._fencing_key(lock_key)))
            payload = self._encode_payload(
                owner_token=resolved_owner,
                fencing_token=fencing_token,
            )
            acquired = await self._client.set(redis_key, payload, nx=True, px=ttl_ms)
            if acquired:
                return LockHandle(
                    acquired=True,
                    lock_key=lock_key,
                    owner_token=resolved_owner,
                    expires_at=self._now() + timedelta(seconds=ttl_seconds),
                    fencing_token=fencing_token,
                )
            blocked = await self._blocked_handle(
                lock_key=lock_key,
                owner_token=resolved_owner,
                redis_key=redis_key,
            )
            if blocked is not None:
                return blocked
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
        released = await self._client.eval(
            _RELEASE_SCRIPT,
            1,
            self._lock_key(lock_key),
            owner_token,
        )
        return bool(released)

    async def extend(
        self,
        lock_key: str,
        *,
        owner_token: str,
        ttl_seconds: int,
    ) -> LockHandle | None:
        self._validate(lock_key=lock_key, ttl_seconds=ttl_seconds, owner_token=owner_token)
        ttl_ms = ttl_seconds * 1000
        result = await self._client.eval(
            _EXTEND_SCRIPT,
            1,
            self._lock_key(lock_key),
            owner_token,
            ttl_ms,
        )
        if not result or int(result[0]) != 1:
            return None
        payload = self._decode_payload(result[1])
        return LockHandle(
            acquired=True,
            lock_key=lock_key,
            owner_token=owner_token,
            expires_at=self._now() + timedelta(seconds=ttl_seconds),
            fencing_token=int(payload["fencing_token"]),
        )

    async def locked(self, lock_key: str) -> bool:
        self._validate_key(lock_key)
        return await self._client.get(self._lock_key(lock_key)) is not None

    async def _blocked_handle(
        self,
        *,
        lock_key: str,
        owner_token: str,
        redis_key: str,
    ) -> LockHandle | None:
        value = await self._client.get(redis_key)
        if value is None:
            return None
        payload = self._decode_payload(value)
        ttl_ms = await self._client.pttl(redis_key)
        return LockHandle(
            acquired=False,
            lock_key=lock_key,
            owner_token=owner_token,
            expires_at=self._now() + timedelta(milliseconds=max(ttl_ms, 0)),
            fencing_token=int(payload["fencing_token"]),
        )

    def _lock_key(self, lock_key: str) -> str:
        return f"{self._prefix}:lock:{lock_key}"

    def _fencing_key(self, lock_key: str) -> str:
        return f"{self._prefix}:fencing:{lock_key}"

    def _encode_payload(self, *, owner_token: str, fencing_token: int) -> str:
        return json.dumps(
            {"owner_token": owner_token, "fencing_token": fencing_token},
            separators=(",", ":"),
        )

    def _decode_payload(self, value: Any) -> dict[str, Any]:
        raw_value = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise AppError(
                "LOCK_STATE_INVALID",
                "Redis lock payload is not valid JSON",
                status_code=500,
            ) from exc
        if "owner_token" not in payload or "fencing_token" not in payload:
            raise AppError(
                "LOCK_STATE_INVALID",
                "Redis lock payload is missing required fields",
                status_code=500,
            )
        return payload

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
