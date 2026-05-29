from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.quotas import QuotaRule, QuotaService, QuotaSubject
from core.security import (
    DEFAULT_UPLOAD_SECURITY_POLICY,
    UploadSecurityPolicy,
    UploadValidationResult,
    validate_upload,
)
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


FileScanStatus = Literal["clean", "infected"]


@dataclass(frozen=True, slots=True)
class FileScanResult:
    status: FileScanStatus
    provider: str
    signature: str | None = None


class FileVirusScanner(Protocol):
    async def scan_file(
        self,
        *,
        tenant_id: str,
        file_name: str,
        content_type: str,
        data: bytes,
        checksum: str,
    ) -> FileScanResult:
        raise NotImplementedError


class AllowAllFileVirusScanner:
    async def scan_file(
        self,
        *,
        tenant_id: str,
        file_name: str,
        content_type: str,
        data: bytes,
        checksum: str,
    ) -> FileScanResult:
        return FileScanResult(status="clean", provider="allow-all")


class FileService:
    def __init__(
        self,
        session: AsyncSession,
        storage: StorageProvider,
        *,
        upload_policy: UploadSecurityPolicy | None = None,
        quota_service: QuotaService | None = None,
        upload_quota_rules: Sequence[QuotaRule] = (),
        virus_scanner: FileVirusScanner | None = None,
        delete_retention_seconds: int = 0,
    ) -> None:
        if delete_retention_seconds < 0:
            raise AppError(
                "VALIDATION_ERROR",
                "delete_retention_seconds must not be negative",
                status_code=400,
            )
        self.session = session
        self.storage = storage
        self.upload_policy = upload_policy or DEFAULT_UPLOAD_SECURITY_POLICY
        self.quota_service = quota_service
        self.upload_quota_rules = tuple(upload_quota_rules)
        self.virus_scanner = virus_scanner or AllowAllFileVirusScanner()
        self.delete_retention_seconds = delete_retention_seconds

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
        validation = self._validate_upload(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            file_name=file_name,
            content_type=content_type,
            data=data,
            file_type=file_type,
            expected_checksum=expected_checksum,
        )
        await self._scan_upload(
            tenant_id=tenant_id,
            validation=validation,
            data=data,
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
        now: datetime | None = None,
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
        file_object.deleted_at = _coerce_utc(now or datetime.now(UTC))
        if self.delete_retention_seconds == 0:
            await self.storage.delete_file(file_object.object_key)
        await self.session.flush()

    async def purge_deleted_files(
        self,
        *,
        tenant_id: str,
        tenant_status: TenantStatus = "active",
        now: datetime | None = None,
    ) -> int:
        assert_tenant_operation_allowed(
            tenant_id=tenant_id,
            status=tenant_status,
            operation="background_cleanup",
        )
        resolved_now = _coerce_utc(now or datetime.now(UTC))
        retention_cutoff = resolved_now - timedelta(seconds=self.delete_retention_seconds)
        result = await self.session.execute(
            select(FileObject).where(
                FileObject.tenant_id == tenant_id,
                FileObject.status == "deleted",
                FileObject.deleted_at.is_not(None),
                FileObject.deleted_at <= retention_cutoff,
            )
        )
        file_objects = list(result.scalars().all())
        for file_object in file_objects:
            await self.storage.delete_file(file_object.object_key)
            file_object.status = "purged"
        await self.session.flush()
        return len(file_objects)

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
    ) -> UploadValidationResult:
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
        return validate_upload(
            file_name=file_name,
            content_type=content_type,
            data=data,
            expected_checksum=expected_checksum,
            policy=self.upload_policy,
        )

    async def _scan_upload(
        self,
        *,
        tenant_id: str,
        validation: UploadValidationResult,
        data: bytes,
    ) -> None:
        result = await self.virus_scanner.scan_file(
            tenant_id=tenant_id,
            file_name=validation.file_name,
            content_type=validation.content_type,
            data=data,
            checksum=validation.checksum,
        )
        if _scan_status(result) == "clean":
            return
        raise AppError(
            "UPLOAD_REJECTED",
            "Upload rejected by virus scan",
            status_code=400,
            details={
                "reason": "virus_detected",
                "provider": str(getattr(result, "provider", "unknown")),
                "signature": getattr(result, "signature", None),
                "file_name": validation.file_name,
            },
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


def _scan_status(result: FileScanResult | Any) -> str:
    return str(getattr(result, "status", "")).lower()


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
