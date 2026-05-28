from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.storage import StorageProvider, file_object_key
from core.tenancy import TenantStatus, assert_tenant_operation_allowed
from platform_apps.files.models import FileObject


@dataclass(frozen=True, slots=True)
class FileDownload:
    file_id: str
    file_name: str
    content_type: str
    checksum: str
    size: int
    data: bytes


class FileService:
    def __init__(self, session: AsyncSession, storage: StorageProvider) -> None:
        self.session = session
        self.storage = storage

    async def upload_bytes(
        self,
        *,
        tenant_id: str,
        owner_type: str,
        owner_id: str,
        file_name: str,
        content_type: str,
        data: bytes,
        file_type: str,
    ) -> FileObject:
        self._validate_upload(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            file_name=file_name,
            content_type=content_type,
            data=data,
            file_type=file_type,
        )
        file_id = str(uuid4())
        object_key = file_object_key(tenant_id=tenant_id, file_id=file_id)
        stored = await self.storage.put_file(object_key, data)
        file_object = FileObject(
            id=file_id,
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            bucket=stored.bucket,
            object_key=stored.object_key,
            file_name=file_name,
            content_type=content_type,
            size=stored.size,
            checksum=stored.checksum,
            file_type=file_type,
            status="available",
        )
        self.session.add(file_object)
        await self.session.flush()
        return file_object

    async def download_bytes(
        self,
        *,
        file_id: str,
        tenant_id: str,
        owner_type: str,
        owner_id: str,
        tenant_status: TenantStatus = "active",
    ) -> FileDownload:
        assert_tenant_operation_allowed(
            tenant_id=tenant_id,
            status=tenant_status,
            operation="file_download",
        )
        file_object = await self._load_available_file(file_id)
        self._assert_file_access(
            file_object,
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        data = await self.storage.get_file(file_object.object_key)
        return FileDownload(
            file_id=file_object.id,
            file_name=file_object.file_name,
            content_type=file_object.content_type,
            checksum=file_object.checksum,
            size=file_object.size,
            data=data,
        )

    async def delete_file(
        self,
        *,
        file_id: str,
        tenant_id: str,
        owner_type: str,
        owner_id: str,
    ) -> None:
        file_object = await self._load_available_file(file_id)
        self._assert_file_access(
            file_object,
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        file_object.status = "deleted"
        file_object.deleted_at = datetime.now(UTC)
        await self.storage.delete_file(file_object.object_key)
        await self.session.flush()

    async def _load_available_file(self, file_id: str) -> FileObject:
        file_object = await self.session.get(FileObject, file_id)
        if file_object is None or file_object.status != "available":
            raise AppError("NOT_FOUND", f"FileObject {file_id!r} not found", status_code=404)
        return file_object

    def _assert_file_access(
        self,
        file_object: FileObject,
        *,
        tenant_id: str,
        owner_type: str,
        owner_id: str,
    ) -> None:
        if file_object.tenant_id != tenant_id:
            raise AppError(
                "TENANT_CONTEXT_CONFLICT",
                "file tenant does not match current tenant",
                status_code=403,
            )
        if file_object.owner_type != owner_type or file_object.owner_id != owner_id:
            raise AppError(
                "PERMISSION_DENIED",
                "file owner scope is not allowed",
                status_code=403,
            )

    def _validate_upload(
        self,
        *,
        tenant_id: str,
        owner_type: str,
        owner_id: str,
        file_name: str,
        content_type: str,
        data: bytes,
        file_type: str,
    ) -> None:
        fields = {
            "tenant_id": tenant_id,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "file_name": file_name,
            "content_type": content_type,
            "file_type": file_type,
        }
        missing = [name for name, value in fields.items() if not value.strip()]
        if missing:
            raise AppError(
                "VALIDATION_ERROR",
                f"file upload missing required fields: {missing}",
                status_code=400,
            )
        if not isinstance(data, bytes) or not data:
            raise AppError("VALIDATION_ERROR", "file upload data is required", status_code=400)
