from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.cli.common import CLI_CONFIRMATION_REQUIRED, error_payload, print_payload
from core.config import get_settings
from core.db import unit_of_work
from core.exceptions import AppError
from core.idempotency import IdempotencyDiagnosis, IdempotencyRecord, IdempotencyStore


def register_idempotency_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("idempotency")
    idempotency_subparsers = parser.add_subparsers(
        dest="idempotency_command",
        required=True,
    )

    expire_parser = idempotency_subparsers.add_parser("expire")
    expire_parser.add_argument("--database-url")
    expire_parser.add_argument("--now")
    expire_parser.add_argument("--yes", action="store_true")
    expire_parser.add_argument("--json", action="store_true", dest="as_json")
    expire_parser.set_defaults(handler=_handle_expire)

    diagnose_parser = idempotency_subparsers.add_parser("diagnose")
    diagnose_parser.add_argument("--database-url")
    diagnose_parser.add_argument("--tenant-id", required=True)
    diagnose_parser.add_argument("--user-id", required=True)
    diagnose_parser.add_argument("--route", required=True)
    diagnose_parser.add_argument("--idempotency-key", required=True)
    diagnose_parser.add_argument("--request-hash")
    diagnose_parser.add_argument("--now")
    diagnose_parser.add_argument("--retry-failed", action="store_true")
    diagnose_parser.add_argument("--json", action="store_true", dest="as_json")
    diagnose_parser.set_defaults(handler=_handle_diagnose)


def _handle_expire(args: argparse.Namespace) -> int:
    if not args.yes:
        print_payload(
            error_payload(
                code=CLI_CONFIRMATION_REQUIRED,
                message="idempotency expire requires --yes",
                command="idempotency expire",
                exit_code=1,
            ),
            as_json=args.as_json,
        )
        return 1
    now = _parse_datetime(args.now)
    payload = asyncio.run(
        _expire_idempotency_records(
            database_url=_database_url(args.database_url),
            now=now,
        )
    )
    print_payload(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


def _handle_diagnose(args: argparse.Namespace) -> int:
    payload = asyncio.run(
        _diagnose_idempotency_record(
            database_url=_database_url(args.database_url),
            tenant_id=args.tenant_id,
            user_id=args.user_id,
            route=args.route,
            idempotency_key=args.idempotency_key,
            request_hash=args.request_hash,
            now=_parse_datetime(args.now),
            retry_failed=args.retry_failed,
        )
    )
    print_payload(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


async def _expire_idempotency_records(
    *,
    database_url: str,
    now: datetime,
) -> dict[str, object]:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            if uow.session is None:
                raise RuntimeError("database session was not initialized")
            expired = await IdempotencyStore(uow.session).expire_before(now)
            return {
                "ok": True,
                "command": "idempotency expire",
                "expired": expired,
                "now": now.isoformat(),
            }
    finally:
        await engine.dispose()


async def _diagnose_idempotency_record(
    *,
    database_url: str,
    tenant_id: str,
    user_id: str,
    route: str,
    idempotency_key: str,
    request_hash: str | None,
    now: datetime,
    retry_failed: bool,
) -> dict[str, object]:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            try:
                diagnosis = await IdempotencyStore(session).diagnose(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    route=route,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    now=now,
                    retry_failed=retry_failed,
                )
            except AppError as exc:
                return error_payload(
                    code=exc.code,
                    message=exc.message,
                    command="idempotency diagnose",
                    exit_code=1,
                    details=dict(exc.details or {}),
                )
            return _diagnosis_payload(diagnosis)
    finally:
        await engine.dispose()


def _diagnosis_payload(diagnosis: IdempotencyDiagnosis) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "command": "idempotency diagnose",
        "diagnosis": diagnosis.status,
        "record": _record_to_dict(diagnosis.record),
    }
    if diagnosis.requested_request_hash is not None:
        payload["requested_request_hash"] = diagnosis.requested_request_hash
    if diagnosis.retry_after_seconds is not None:
        payload["retry_after_seconds"] = diagnosis.retry_after_seconds
    if diagnosis.status == "replayable" and diagnosis.record is not None:
        payload["replay"] = {
            "response_code": diagnosis.record.response_code,
            "response_body": diagnosis.record.response_body,
            "task_id": diagnosis.record.task_id,
            "outbox_event_id": diagnosis.record.outbox_event_id,
        }
    return payload


def _record_to_dict(record: IdempotencyRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "user_id": record.user_id,
        "route": record.route,
        "idempotency_key": record.idempotency_key,
        "request_hash": record.request_hash,
        "status": record.status,
        "response_code": record.response_code,
        "response_body": record.response_body,
        "task_id": record.task_id,
        "outbox_event_id": record.outbox_event_id,
        "locked_until": record.locked_until.isoformat() if record.locked_until else None,
        "expires_at": record.expires_at.isoformat(),
    }


def _database_url(value: str | None) -> str:
    return value or get_settings().database.url


def _parse_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
