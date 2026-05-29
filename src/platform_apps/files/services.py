from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.quotas import QuotaRule, QuotaService, QuotaSubject
from core.security import DEFAULT_UPLOAD_SECURITY_POLICY, UploadSecurityPolicy, validate_upload
from core.storage import StorageProvider, file_object_key
from core.tenancy import TenantStatus, assert_tenant_operation_allowed
from platform_apps.files.models import FileObject

if TYPE_CHECKING:
    from core.permissions import AuthorizationService


@dataclass(frozen=True, slots=True)
class FileDownload:
    file_id: str
    file_name: str
    content_type: str
    checksum: str
    size: int
    data: bytes


class FileService:
    def __init__(
        self,
        session: AsyncSession,
        storage: StorageProvider,
        *,
        upload_policy: UploadSecurityPolicy | None = None,
        quota_service: QuotaService | None = None,
        upload_quota_rules: Sequence[QuotaRule] = (),
    ) -> None:
        self.session = session
        self.storage = storage
        self.upload_policy = upload_policy or DEFAULT_UPLOAD_SECURITY_POLICY
        self.quota_service = quota_service
        self.upload_quota_rules = tuple(upload_quota_rules)

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
        expected_checksum: str | None = None,
        user_id: str | None = None,
        authorization: AuthorizationService | None = None,
        request_id: str | None = None,
    ) -> FileObject:
        await self._require_file_permission(
            action="upload",
            tenant_id=tenant_id,
            user_id=user_id,
            authorization=authorization,
            resource_id=owner_id,
            request_id=request_id,
        )
        self._validate_upload(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            file_name=file_name,
            content_type=content_type,
            data=data,
            file_type=file_type,
            expected_checksum=expected_checksum,
        )
        reserved_quotas = await self._reserve_upload_quotas(tenant_id=tenant_id, data=data)
        try:
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
        except Exception:
            await self._release_upload_quotas(
                tenant_id=tenant_id,
                reserved_quotas=reserved_quotas,
            )
            raise

    async def download_bytes(
        self,
        *,
        file_id: str,
        tenant_id: str,
        owner_type: str,
        owner_id: str,
        tenant_status: TenantStatus = "active",
        user_id: str | None = None,
        authorization: AuthorizationService | None = None,
        request_id: str | None = None,
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
        await self._require_file_permission(
            action="download",
            tenant_id=tenant_id,
            user_id=user_id,
            authorization=authorization,
            resource_id=file_object.id,
            request_id=request_id,
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
        user_id: str | None = None,
        authorization: AuthorizationService | None = None,
        request_id: str | None = None,
    ) -> None:
        file_object = await self._load_available_file(file_id)
        self._assert_file_access(
            file_object,
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        await self._require_file_permission(
            action="delete",
            tenant_id=tenant_id,
            user_id=user_id,
            authorization=authorization,
            resource_id=file_object.id,
            request_id=request_id,
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

    async def _require_file_permission(
        self,
        *,
        action: str,
        tenant_id: str,
        user_id: str | None,
        authorization: AuthorizationService | None,
        resource_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        if authorization is None:
            raise AppError(
                "PERMISSION_DENIED",
                "File permission authorization is required",
                status_code=403,
                details={"action": action, "resource": "file"},
            )
        if user_id is None or not user_id.strip():
            raise AppError(
                "VALIDATION_ERROR",
                "user_id is required for file authorization",
                status_code=400,
                details={"action": action, "resource": "file"},
            )
        await authorization.require(
            user_id=user_id,
            tenant_id=tenant_id,
            resource="file",
            action=action,
            resource_id=resource_id,
            request_id=request_id,
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
        expected_checksum: str | None,
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
        validate_upload(
            file_name=file_name,
            content_type=content_type,
            data=data,
            expected_checksum=expected_checksum,
            policy=self.upload_policy,
        )

    async def _reserve_upload_quotas(
        self,
        *,
        tenant_id: str,
        data: bytes,
    ) -> list[tuple[QuotaRule, int]]:
        if not self.upload_quota_rules:
            return []
        if self.quota_service is None:
            raise AppError(
                "VALIDATION_ERROR",
                "quota_service is required when upload_quota_rules are configured",
                status_code=400,
            )
        subject = QuotaSubject(tenant_id=tenant_id)
        reserved: list[tuple[QuotaRule, int]] = []
        for rule in self.upload_quota_rules:
            amount = len(data) if rule.metric == "storage_bytes" else 1
            try:
                await self.quota_service.require_reserve(rule, subject, amount=amount)
            except Exception:
                await self._release_upload_quotas(
                    tenant_id=tenant_id,
                    reserved_quotas=reserved,
                )
                raise
            reserved.append((rule, amount))
        return reserved

    async def _release_upload_quotas(
        self,
        *,
        tenant_id: str,
        reserved_quotas: list[tuple[QuotaRule, int]],
    ) -> None:
        if self.quota_service is None:
            return
        subject = QuotaSubject(tenant_id=tenant_id)
        for rule, amount in reversed(reserved_quotas):
            await self.quota_service.release(rule, subject, amount=amount)
