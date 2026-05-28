from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.exceptions import AppError
from core.permissions import AuthorizationService, ProjectedPolicy
from core.security import UploadSecurityPolicy
from core.storage import LocalStorageProvider
from platform_apps.audit import AuditLog, AuditService
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


def _grant_file_permissions(
    session: AsyncSession,
    *,
    user_id: str = "user-1",
    tenant_id: str = "tenant-a",
    actions: tuple[str, ...] = ("upload", "download", "delete"),
) -> None:
    for action in actions:
        session.add(
            ProjectedPolicy(
                tenant_id=tenant_id,
                subject=f"user:{user_id}",
                resource="file",
                action=action,
                effect="allow",
                role_grant_id=f"grant-{user_id}-{action}",
                policy_version=1,
            )
        )


@pytest.mark.asyncio
async def test_upload_requires_file_permission_authorization(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as denied:
            await FileService(uow.session, storage).upload_bytes(
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
                file_name="proposal.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                data=b"docx-bytes",
                file_type="upload",
            )

    assert denied.value.code == "PERMISSION_DENIED"
    assert denied.value.details == {"action": "upload", "resource": "file"}
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
async def test_upload_writes_storage_and_metadata(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload",))
        file_object = await FileService(uow.session, storage).upload_bytes(
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            file_name="proposal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"docx-bytes",
            file_type="upload",
            user_id="user-1",
            authorization=AuthorizationService(uow.session),
        )

    assert file_object.tenant_id == "tenant-a"
    assert file_object.bucket == "local-files"
    assert file_object.object_key == f"tenants/tenant-a/files/{file_object.id}/original.bin"
    assert file_object.size == len(b"docx-bytes")
    assert file_object.checksum
    assert file_object.status == "available"
    assert await storage.get_file(file_object.object_key) == b"docx-bytes"

    async with session_factory() as session:
        persisted = await session.get(FileObject, file_object.id)
        assert persisted is not None
        assert persisted.object_key == file_object.object_key


@pytest.mark.asyncio
async def test_upload_rejects_file_before_storage_when_security_policy_fails(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload",))
        with pytest.raises(AppError) as rejected:
            await FileService(
                uow.session,
                storage,
                upload_policy=UploadSecurityPolicy(max_bytes=4),
            ).upload_bytes(
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
                file_name="proposal.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                data=b"docx-bytes",
                file_type="upload",
                user_id="user-1",
                authorization=AuthorizationService(uow.session),
            )

    assert rejected.value.code == "UPLOAD_REJECTED"
    assert list(tmp_path.rglob("*")) == []
    async with session_factory() as session:
        persisted = await session.scalar(select(FileObject))
        assert persisted is None


@pytest.mark.asyncio
async def test_download_enforces_tenant_owner_and_lifecycle_gate(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload", "download"))
        file_object = await FileService(uow.session, storage).upload_bytes(
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            file_name="proposal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"docx-bytes",
            file_type="upload",
            user_id="user-1",
            authorization=AuthorizationService(uow.session),
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        download = await FileService(uow.session, storage).download_bytes(
            file_id=file_object.id,
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            tenant_status="active",
            user_id="user-1",
            authorization=AuthorizationService(uow.session),
        )

    assert download.file_name == "proposal.docx"
    assert download.content_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert download.data == b"docx-bytes"

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as tenant_error:
            await FileService(uow.session, storage).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-b",
                owner_type="bid",
                owner_id="bid-1",
            )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as owner_error:
            await FileService(uow.session, storage).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-a",
                owner_type="project",
                owner_id="project-1",
            )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as lifecycle_error:
            await FileService(uow.session, storage).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
                tenant_status="deleting",
            )

    assert tenant_error.value.code == "TENANT_CONTEXT_CONFLICT"
    assert owner_error.value.code == "PERMISSION_DENIED"
    assert lifecycle_error.value.code == "TENANT_STATE_FORBIDDEN"


@pytest.mark.asyncio
async def test_delete_marks_metadata_and_removes_storage_object(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload", "delete"))
        file_object = await FileService(uow.session, storage).upload_bytes(
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            file_name="proposal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"docx-bytes",
            file_type="upload",
            user_id="user-1",
            authorization=AuthorizationService(uow.session),
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await FileService(uow.session, storage).delete_file(
            file_id=file_object.id,
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            user_id="user-1",
            authorization=AuthorizationService(uow.session),
        )

    async with session_factory() as session:
        persisted = await session.scalar(select(FileObject).where(FileObject.id == file_object.id))
        assert persisted is not None
        assert persisted.status == "deleted"
    assert await storage.exists(file_object.object_key) is False

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as download_deleted:
            await FileService(uow.session, storage).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
            )

    assert download_deleted.value.code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_download_can_require_projected_file_permission_and_audit_denials(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload", "download"))
        file_object = await FileService(uow.session, storage).upload_bytes(
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            file_name="proposal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"docx-bytes",
            file_type="upload",
            user_id="user-1",
            authorization=AuthorizationService(uow.session),
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        download = await FileService(uow.session, storage).download_bytes(
            file_id=file_object.id,
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            user_id="user-1",
            authorization=AuthorizationService(uow.session),
        )

    assert download.data == b"docx-bytes"

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as denied:
            await FileService(uow.session, storage).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
                user_id="user-2",
                request_id="req-file-denied",
                authorization=AuthorizationService(
                    uow.session,
                    audit=AuditService(uow.session),
                ),
            )

    audit_logs = await _audit_logs(session_factory)
    assert denied.value.code == "PERMISSION_DENIED"
    assert len(audit_logs) == 1
    assert audit_logs[0].action == "authorization.denied"
    assert audit_logs[0].resource_type == "file"
    assert audit_logs[0].resource_id == file_object.id
    assert audit_logs[0].actor_id == "user-2"
    assert audit_logs[0].request_id == "req-file-denied"


async def _audit_logs(session_factory: async_sessionmaker[AsyncSession]) -> list[AuditLog]:
    async with session_factory() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.created_at))
        audit_logs = list(result.scalars().all())
        for audit_log in audit_logs:
            session.expunge(audit_log)
        return audit_logs
