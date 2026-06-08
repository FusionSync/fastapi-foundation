import sys
import types
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.apps import TaskHandlerSpec
from core.base.models import BaseModel
from core.db import unit_of_work
from core.idempotency import IdempotencyRecord, IdempotencyStore
from core.permissions import AuthorizationService, ProjectedPolicy
from core.storage import LocalStorageProvider
from core.tasks import SyncTaskProvider, TaskEnvelope, TaskRegistry, TaskRun, TaskRunRepository
from platform_apps.accounts import AccountsService, User
from platform_apps.files import FileObject, FileService


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_idempotency_guard_replays_account_create_without_duplicate_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from core.idempotency import IdempotencyMutationGuard

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        guard = IdempotencyMutationGuard(IdempotencyStore(uow.session))
        first = await guard.run(
            tenant_id="tenant-a",
            user_id="actor-1",
            route="POST /platform/accounts/users",
            idempotency_key="idem-account-1",
            request_payload={"email": "owner@example.com", "display_name": "Owner"},
            handler=lambda: AccountsService(uow.session).create_user(
                email="owner@example.com",
                display_name="Owner",
            ),
            response_code="USER_CREATED",
            response_builder=lambda user: {"user_id": user.id, "email": user.email},
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        guard = IdempotencyMutationGuard(IdempotencyStore(uow.session))
        replayed = await guard.run(
            tenant_id="tenant-a",
            user_id="actor-1",
            route="POST /platform/accounts/users",
            idempotency_key="idem-account-1",
            request_payload={"email": "owner@example.com", "display_name": "Owner"},
            handler=lambda: (_ for _ in ()).throw(AssertionError("handler must not rerun")),
            response_code="USER_CREATED",
            response_builder=lambda user: {"user_id": user.id},
        )

    assert first.outcome == "started"
    assert first.response_body["email"] == "owner@example.com"
    assert replayed.outcome == "replayed"
    assert replayed.response_body == first.response_body
    assert await _row_count(session_factory, User) == 1


@pytest.mark.asyncio
async def test_idempotency_guard_replays_file_upload_without_second_storage_write(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    from core.idempotency import IdempotencyMutationGuard

    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_permission(uow.session, resource="file", action="upload")
        guard = IdempotencyMutationGuard(IdempotencyStore(uow.session))
        first = await guard.run(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /platform/files/upload",
            idempotency_key="idem-file-1",
            request_payload={"file_name": "proposal.docx", "checksum": "sha256-placeholder"},
            handler=lambda: FileService(uow.session, storage).upload_bytes(
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
                file_name="proposal.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                data=b"docx-bytes",
                file_type="upload",
                user_id="user-1",
                authorization=AuthorizationService(uow.session),
            ),
            response_code="FILE_UPLOADED",
            response_builder=lambda file_object: {
                "file_id": file_object.id,
                "object_key": file_object.object_key,
            },
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_permission(uow.session, resource="file", action="upload")
        guard = IdempotencyMutationGuard(IdempotencyStore(uow.session))
        replayed = await guard.run(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /platform/files/upload",
            idempotency_key="idem-file-1",
            request_payload={"file_name": "proposal.docx", "checksum": "sha256-placeholder"},
            handler=lambda: (_ for _ in ()).throw(AssertionError("handler must not rerun")),
            response_code="FILE_UPLOADED",
            response_builder=lambda file_object: {"file_id": file_object.id},
        )

    assert first.outcome == "started"
    assert replayed.outcome == "replayed"
    assert replayed.response_body == first.response_body
    assert await _row_count(session_factory, FileObject) == 1
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1


@pytest.mark.asyncio
async def test_idempotency_guard_binds_task_submit_to_task_id(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from core.idempotency import IdempotencyMutationGuard

    calls: list[str] = []

    async def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        calls.append(envelope.task_id)
        return {"task_id": envelope.task_id}

    task_registry = _task_registry(monkeypatch, refresh)
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        guard = IdempotencyMutationGuard(IdempotencyStore(uow.session))
        first = await guard.run(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /core/tasks/submit",
            idempotency_key="idem-task-1",
            request_payload={"task_type": "example.refresh", "payload": {"value": "ok"}},
            handler=lambda: SyncTaskProvider(
                task_registry,
                task_repository=TaskRunRepository(uow.session),
            ).submit(
                TaskEnvelope(
                    task_id="task-1",
                    task_type="example.refresh",
                    tenant_id="tenant-a",
                    payload={"value": "ok"},
                    idempotency_key="task-idem-1",
                    request_id="req-1",
                )
            ),
            response_code="TASK_SUBMITTED",
            response_builder=lambda result: result.to_dict(),
            task_id_builder=lambda result: result.task_id,
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        guard = IdempotencyMutationGuard(IdempotencyStore(uow.session))
        replayed = await guard.run(
            tenant_id="tenant-a",
            user_id="user-1",
            route="POST /core/tasks/submit",
            idempotency_key="idem-task-1",
            request_payload={"task_type": "example.refresh", "payload": {"value": "ok"}},
            handler=lambda: (_ for _ in ()).throw(AssertionError("handler must not rerun")),
            response_code="TASK_SUBMITTED",
            response_builder=lambda result: result.to_dict(),
            task_id_builder=lambda result: result.task_id,
        )

    record = await _idempotency_record(session_factory, "idem-task-1")
    assert first.outcome == "started"
    assert replayed.outcome == "replayed"
    assert replayed.response_body == first.response_body
    assert replayed.task_id == "task-1"
    assert record is not None
    assert record.task_id == "task-1"
    assert calls == ["task-1"]
    assert await _row_count(session_factory, TaskRun) == 1


def _grant_permission(
    session: AsyncSession,
    *,
    user_id: str = "user-1",
    tenant_id: str = "tenant-a",
    resource: str,
    action: str,
) -> None:
    session.add(
        ProjectedPolicy(
            tenant_id=tenant_id,
            subject=f"user:{user_id}",
            resource=resource,
            action=action,
            effect="allow",
            role_grant_id=f"grant-{user_id}-{resource}-{action}-{uuid4()}",
            policy_version=1,
        )
    )


def _task_registry(monkeypatch: pytest.MonkeyPatch, handler) -> TaskRegistry:
    handler_module = types.ModuleType("idem_task_handlers")
    handler_module.refresh = handler
    monkeypatch.setitem(sys.modules, "idem_task_handlers", handler_module)
    registry = TaskRegistry()
    registry.register(
        "task_app",
        TaskHandlerSpec(
            task_type="example.refresh",
            handler_path="idem_task_handlers.refresh",
            queue="default",
        ),
    )
    return registry


async def _row_count(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[User] | type[FileObject] | type[TaskRun],
) -> int:
    async with session_factory() as session:
        value = await session.scalar(select(func.count()).select_from(model))
        return int(value or 0)


async def _idempotency_record(
    session_factory: async_sessionmaker[AsyncSession],
    idempotency_key: str,
) -> IdempotencyRecord | None:
    async with session_factory() as session:
        result = await session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == idempotency_key
            )
        )
        record = result.scalars().first()
        if record is not None:
            session.expunge(record)
        return record
