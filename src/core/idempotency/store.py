from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.idempotency.models import IdempotencyRecord

IdempotencyOutcome = Literal["started", "replayed"]
IdempotencyDiagnosisStatus = Literal[
    "missing",
    "expired_reusable",
    "request_hash_conflict",
    "replayable",
    "in_progress",
    "failed_requires_retry_opt_in",
    "retryable_failed",
]


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    outcome: IdempotencyOutcome
    record: IdempotencyRecord


@dataclass(frozen=True, slots=True)
class IdempotencyDiagnosis:
    status: IdempotencyDiagnosisStatus
    record: IdempotencyRecord | None
    requested_request_hash: str | None = None
    retry_after_seconds: int | None = None


class IdempotencyStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(
        self,
        *,
        tenant_id: str,
        user_id: str,
        route: str,
        idempotency_key: str,
        request_hash: str,
        ttl_seconds: int = 24 * 60 * 60,
        lock_seconds: int = 60,
        now: datetime | None = None,
        retry_failed: bool = False,
    ) -> IdempotencyClaim:
        resolved_now = _coerce_utc(now or datetime.now(UTC))
        self._validate_scope(tenant_id, user_id, route, idempotency_key, request_hash)
        inserted = await self._insert_processing_record(
            tenant_id=tenant_id,
            user_id=user_id,
            route=route,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            ttl_seconds=ttl_seconds,
            lock_seconds=lock_seconds,
            now=resolved_now,
        )
        if inserted is not None:
            return inserted

        existing = await self._get_record(
            tenant_id=tenant_id,
            user_id=user_id,
            route=route,
            idempotency_key=idempotency_key,
        )
        if existing is None:
            raise AppError(
                "CONFLICT",
                "Idempotency claim raced but no record was found",
                status_code=409,
            )
        return await self._handle_existing(
            existing,
            request_hash=request_hash,
            ttl_seconds=ttl_seconds,
            lock_seconds=lock_seconds,
            now=resolved_now,
            retry_failed=retry_failed,
        )

    async def mark_succeeded(
        self,
        record: IdempotencyRecord,
        *,
        response_code: str,
        response_body: Any,
        task_id: str | None = None,
        outbox_event_id: str | None = None,
    ) -> None:
        record.status = "succeeded"
        record.response_code = response_code
        record.response_body = response_body
        record.task_id = task_id
        record.outbox_event_id = outbox_event_id
        record.locked_until = None
        await self.session.flush()

    async def mark_failed(
        self,
        record: IdempotencyRecord,
        *,
        response_code: str,
        response_body: Any,
    ) -> None:
        record.status = "failed"
        record.response_code = response_code
        record.response_body = response_body
        record.locked_until = None
        await self.session.flush()

    async def expire_before(self, now: datetime | None = None) -> int:
        resolved_now = _coerce_utc(now or datetime.now(UTC))
        result = await self.session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.status != "expired",
                IdempotencyRecord.expires_at <= resolved_now,
            )
        )
        records = list(result.scalars().all())
        for record in records:
            record.status = "expired"
            record.locked_until = None
        await self.session.flush()
        return len(records)

    async def diagnose(
        self,
        *,
        tenant_id: str,
        user_id: str,
        route: str,
        idempotency_key: str,
        request_hash: str | None = None,
        now: datetime | None = None,
        retry_failed: bool = False,
    ) -> IdempotencyDiagnosis:
        resolved_now = _coerce_utc(now or datetime.now(UTC))
        self._validate_lookup_scope(
            tenant_id=tenant_id,
            user_id=user_id,
            route=route,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        record = await self._get_record(
            tenant_id=tenant_id,
            user_id=user_id,
            route=route,
            idempotency_key=idempotency_key,
        )
        if record is None:
            return IdempotencyDiagnosis(status="missing", record=None)

        expires_at = _coerce_utc(record.expires_at)
        if expires_at <= resolved_now or record.status == "expired":
            return IdempotencyDiagnosis(
                status="expired_reusable",
                record=record,
                requested_request_hash=request_hash,
            )

        if request_hash is not None and record.request_hash != request_hash:
            return IdempotencyDiagnosis(
                status="request_hash_conflict",
                record=record,
                requested_request_hash=request_hash,
            )

        if record.status == "succeeded":
            return IdempotencyDiagnosis(
                status="replayable",
                record=record,
                requested_request_hash=request_hash,
            )

        if record.status == "processing":
            locked_until = _coerce_utc(record.locked_until)
            return IdempotencyDiagnosis(
                status="in_progress",
                record=record,
                requested_request_hash=request_hash,
                retry_after_seconds=_retry_after_seconds(locked_until, resolved_now),
            )

        return IdempotencyDiagnosis(
            status="retryable_failed" if retry_failed else "failed_requires_retry_opt_in",
            record=record,
            requested_request_hash=request_hash,
        )

    async def _insert_processing_record(
        self,
        *,
        tenant_id: str,
        user_id: str,
        route: str,
        idempotency_key: str,
        request_hash: str,
        ttl_seconds: int,
        lock_seconds: int,
        now: datetime,
    ) -> IdempotencyClaim | None:
        record = IdempotencyRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            route=route,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status="processing",
            locked_until=now + timedelta(seconds=lock_seconds),
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
        except IntegrityError:
            return None
        return IdempotencyClaim(outcome="started", record=record)

    async def _get_record(
        self,
        *,
        tenant_id: str,
        user_id: str,
        route: str,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        result = await self.session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == tenant_id,
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.route == route,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        return result.scalars().first()

    async def _handle_existing(
        self,
        record: IdempotencyRecord,
        *,
        request_hash: str,
        ttl_seconds: int,
        lock_seconds: int,
        now: datetime,
        retry_failed: bool,
    ) -> IdempotencyClaim:
        expires_at = _coerce_utc(record.expires_at)
        if expires_at <= now:
            await self._restart_processing(record, request_hash, ttl_seconds, lock_seconds, now)
            return IdempotencyClaim(outcome="started", record=record)

        if record.request_hash != request_hash:
            raise AppError(
                "IDEMPOTENCY_KEY_CONFLICT",
                "Idempotency key was already used with a different request payload",
                status_code=409,
            )

        if record.status == "succeeded":
            return IdempotencyClaim(outcome="replayed", record=record)

        if record.status == "processing":
            locked_until = _coerce_utc(record.locked_until)
            if locked_until is not None and locked_until <= now:
                await self._restart_processing(record, request_hash, ttl_seconds, lock_seconds, now)
                return IdempotencyClaim(outcome="started", record=record)
            raise AppError(
                "IDEMPOTENCY_IN_PROGRESS",
                "Idempotency request is already processing",
                status_code=409,
                details={"retry_after": _retry_after_seconds(locked_until, now)},
            )

        if record.status == "failed" and not retry_failed:
            raise AppError(
                "CONFLICT",
                "Failed idempotency request cannot be retried without explicit opt-in",
                status_code=409,
            )

        await self._restart_processing(record, request_hash, ttl_seconds, lock_seconds, now)
        return IdempotencyClaim(outcome="started", record=record)

    async def _restart_processing(
        self,
        record: IdempotencyRecord,
        request_hash: str,
        ttl_seconds: int,
        lock_seconds: int,
        now: datetime,
    ) -> None:
        record.request_hash = request_hash
        record.status = "processing"
        record.response_code = None
        record.response_body = None
        record.task_id = None
        record.outbox_event_id = None
        record.locked_until = now + timedelta(seconds=lock_seconds)
        record.expires_at = now + timedelta(seconds=ttl_seconds)
        await self.session.flush()

    def _validate_scope(
        self,
        tenant_id: str,
        user_id: str,
        route: str,
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        values = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "route": route,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
        }
        empty = [name for name, value in values.items() if not value.strip()]
        if empty:
            raise AppError(
                "VALIDATION_ERROR",
                f"Idempotency fields must be non-empty: {empty}",
                status_code=400,
            )

    def _validate_lookup_scope(
        self,
        *,
        tenant_id: str,
        user_id: str,
        route: str,
        idempotency_key: str,
        request_hash: str | None,
    ) -> None:
        values = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "route": route,
            "idempotency_key": idempotency_key,
        }
        if request_hash is not None:
            values["request_hash"] = request_hash
        empty = [name for name, value in values.items() if not value.strip()]
        if empty:
            raise AppError(
                "VALIDATION_ERROR",
                f"Idempotency fields must be non-empty: {empty}",
                status_code=400,
            )


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _retry_after_seconds(locked_until: datetime | None, now: datetime) -> int:
    if locked_until is None:
        return 0
    return max(0, int((locked_until - now).total_seconds()))
