from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.exceptions import AppError
from core.permissions import AuthorizationService, ProjectedPolicy
from core.quotas import MemoryQuotaUsageStore, QuotaRule, QuotaService
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
        _grant_permission(
            session,
            user_id=user_id,
            tenant_id=tenant_id,
            resource="file",
            action=action,
        )


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


class RejectingVirusScanner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def scan_file(
        self,
        *,
        tenant_id: str,
        file_name: str,
        content_type: str,
        data: bytes,
        checksum: str,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "file_name": file_name,
                "content_type": content_type,
                "data": data,
                "checksum": checksum,
            }
        )
        return SimpleNamespace(
            status="infected",
            provider="clamav",
            signature="EICAR-Test-File",
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
async def test_upload_rejects_infected_file_before_storage_and_metadata(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    scanner = RejectingVirusScanner()

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload",))
        with pytest.raises(AppError) as rejected:
            await FileService(
                uow.session,
                storage,
                virus_scanner=scanner,
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
    assert rejected.value.details["reason"] == "virus_detected"
    assert rejected.value.details["provider"] == "clamav"
    assert rejected.value.details["signature"] == "EICAR-Test-File"
    assert scanner.calls[0]["tenant_id"] == "tenant-a"
    assert scanner.calls[0]["data"] == b"docx-bytes"
    assert list(tmp_path.rglob("*")) == []
    async with session_factory() as session:
        persisted = await session.scalar(select(FileObject))
        assert persisted is None


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
async def test_upload_reserves_storage_quota_before_writing_storage(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    quota_store = MemoryQuotaUsageStore()
    quota_service = QuotaService(quota_store)
    quota_rule = QuotaRule(metric="storage_bytes", limit=4, scope="tenant")

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload",))
        with pytest.raises(AppError) as exceeded:
            await FileService(
                uow.session,
                storage,
                quota_service=quota_service,
                upload_quota_rules=(quota_rule,),
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

    assert exceeded.value.code == "QUOTA_EXCEEDED"
    assert await quota_store.get_usage("quota:storage_bytes:tenant_id=tenant-a") == 0
    assert list(tmp_path.rglob("*")) == []
    async with session_factory() as session:
        persisted = await session.scalar(select(FileObject))
        assert persisted is None


@pytest.mark.asyncio
async def test_upload_rolls_back_earlier_quota_reservations_when_later_quota_fails(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    quota_store = MemoryQuotaUsageStore()
    quota_service = QuotaService(quota_store)
    file_count = QuotaRule(metric="file_count", limit=10, scope="tenant")
    storage_bytes = QuotaRule(metric="storage_bytes", limit=4, scope="tenant")

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload",))
        with pytest.raises(AppError) as exceeded:
            await FileService(
                uow.session,
                storage,
                quota_service=quota_service,
                upload_quota_rules=(file_count, storage_bytes),
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

    assert exceeded.value.code == "QUOTA_EXCEEDED"
    assert await quota_store.get_usage("quota:file_count:tenant_id=tenant-a") == 0
    assert await quota_store.get_usage("quota:storage_bytes:tenant_id=tenant-a") == 0


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
async def test_retention_cleanup_purges_deleted_objects_after_lifecycle_gate(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    deleted_at = datetime(2026, 5, 29, 8, 0, tzinfo=UTC)
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload", "delete"))
        file_service = FileService(
            uow.session,
            storage,
            delete_retention_seconds=3600,
        )
        file_object = await file_service.upload_bytes(
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
        await file_service.delete_file(
            file_id=file_object.id,
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            user_id="user-1",
            authorization=AuthorizationService(uow.session),
            now=deleted_at,
        )

    assert await storage.exists(file_object.object_key) is True
    async with session_factory() as session:
        persisted = await session.get(FileObject, file_object.id)
        assert persisted is not None
        assert persisted.status == "deleted"

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as suspended_cleanup:
            await FileService(
                uow.session,
                storage,
                delete_retention_seconds=3600,
            ).purge_deleted_files(
                tenant_id="tenant-a",
                tenant_status="suspended",
                now=deleted_at + timedelta(hours=2),
            )

    assert suspended_cleanup.value.code == "TENANT_STATE_FORBIDDEN"
    assert await storage.exists(file_object.object_key) is True

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        early_count = await FileService(
            uow.session,
            storage,
            delete_retention_seconds=3600,
        ).purge_deleted_files(
            tenant_id="tenant-a",
            tenant_status="deleting",
            now=deleted_at + timedelta(minutes=30),
        )

    assert early_count == 0
    assert await storage.exists(file_object.object_key) is True

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        purged_count = await FileService(
            uow.session,
            storage,
            delete_retention_seconds=3600,
        ).purge_deleted_files(
            tenant_id="tenant-a",
            tenant_status="deleting",
            now=deleted_at + timedelta(hours=2),
        )

    assert purged_count == 1
    assert await storage.exists(file_object.object_key) is False
    async with session_factory() as session:
        purged = await session.get(FileObject, file_object.id)
        assert purged is not None
        assert purged.status == "purged"


@pytest.mark.asyncio
async def test_resource_authorization_adapter_gates_file_owner_scope_operations(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    from platform_apps.files import AuthorizationServiceFileResourceAdapter

    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    resource_authorization = AuthorizationServiceFileResourceAdapter()

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload",))
        with pytest.raises(AppError) as upload_denied:
            await FileService(
                uow.session,
                storage,
                resource_authorization=resource_authorization,
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

    assert upload_denied.value.code == "PERMISSION_DENIED"
    assert upload_denied.value.details["resource"] == "bid"
    assert upload_denied.value.details["action"] == "write"
    assert list(tmp_path.rglob("*")) == []
    async with session_factory() as session:
        assert await session.scalar(select(FileObject)) is None

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, actions=("upload",))
        _grant_permission(uow.session, resource="bid", action="write")
        file_object = await FileService(
            uow.session,
            storage,
            resource_authorization=resource_authorization,
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

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, user_id="user-2", actions=("download",))
        with pytest.raises(AppError) as download_denied:
            await FileService(
                uow.session,
                storage,
                resource_authorization=resource_authorization,
            ).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
                user_id="user-2",
                authorization=AuthorizationService(uow.session),
            )

    assert download_denied.value.code == "PERMISSION_DENIED"
    assert download_denied.value.details["resource"] == "bid"
    assert download_denied.value.details["action"] == "read"

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, user_id="user-2", actions=("download", "delete"))
        _grant_permission(uow.session, user_id="user-2", resource="bid", action="read")
        download = await FileService(
            uow.session,
            storage,
            resource_authorization=resource_authorization,
        ).download_bytes(
            file_id=file_object.id,
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            user_id="user-2",
            authorization=AuthorizationService(uow.session),
        )
        with pytest.raises(AppError) as delete_denied:
            await FileService(
                uow.session,
                storage,
                resource_authorization=resource_authorization,
            ).delete_file(
                file_id=file_object.id,
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
                user_id="user-2",
                authorization=AuthorizationService(uow.session),
            )

    assert download.data == b"docx-bytes"
    assert delete_denied.value.code == "PERMISSION_DENIED"
    assert delete_denied.value.details["resource"] == "bid"
    assert delete_denied.value.details["action"] == "write"

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        _grant_file_permissions(uow.session, user_id="user-2", actions=("delete",))
        _grant_permission(uow.session, user_id="user-2", resource="bid", action="write")
        await FileService(
            uow.session,
            storage,
            resource_authorization=resource_authorization,
        ).delete_file(
            file_id=file_object.id,
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            user_id="user-2",
            authorization=AuthorizationService(uow.session),
        )

    assert await storage.exists(file_object.object_key) is False


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
