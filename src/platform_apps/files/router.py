from __future__ import annotations

import base64
import binascii
import importlib
from io import BytesIO
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.base import create_router
from core.context import get_current_context
from core.db import unit_of_work
from core.exceptions import AppError
from core.permissions import AuthorizationService
from core.security import DEFAULT_UPLOAD_SECURITY_POLICY, UploadSecurityPolicy
from core.serialization import Envelope, ListEnvelope, Pagination, ok, ok_list
from core.storage import MultipartUploadPart, StorageProvider
from platform_apps.files.models import FileObject
from platform_apps.files.schemas import (
    BatchFileUploadRead,
    BatchFileUploadRequest,
    FileDeleteRead,
    FileDownloadRead,
    FileObjectRead,
    MultipartUploadCompleteRequest,
    MultipartUploadInitiatedRead,
    MultipartUploadInitiateRequest,
    PresignedDownloadRead,
    PresignedDownloadRequest,
    PresignedUploadRead,
    PresignedUploadRequest,
)
from platform_apps.files.services import FileService

router = create_router(
    "/platform/files",
    tags=["platform-files"],
    permissions=["file:upload"],
    tenant_operation="write",
)
read_router = create_router(
    "/platform/files",
    tags=["platform-files"],
    permissions=["file:download"],
)
delete_router = create_router(
    "/platform/files",
    tags=["platform-files"],
    permissions=["file:delete"],
    tenant_operation="write",
)


@router.post("/batch", response_model=Envelope[BatchFileUploadRead])
async def upload_batch(
    request: Request,
    payload: BatchFileUploadRequest,
) -> dict[str, object]:
    context = _request_context()
    files = [
        {
            "file_name": item.file_name,
            "content_type": item.content_type,
            "file_type": item.file_type,
            "expected_checksum": item.expected_checksum,
            "data": _decode_base64(item.content_base64),
        }
        for item in payload.files
    ]
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        uploaded = await (
            await _service(request, session, tenant_id=context.tenant_id)
        ).upload_batch_bytes(
            tenant_id=context.tenant_id,
            owner_type=payload.owner_type,
            owner_id=payload.owner_id,
            files=files,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return ok({"files": [_file_read(file_object) for file_object in uploaded]})


@router.post("/presigned-upload", response_model=Envelope[PresignedUploadRead])
async def create_presigned_upload(
    request: Request,
    payload: PresignedUploadRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        result = await (
            await _service(request, session, tenant_id=context.tenant_id)
        ).create_presigned_upload(
            tenant_id=context.tenant_id,
            owner_type=payload.owner_type,
            owner_id=payload.owner_id,
            file_name=payload.file_name,
            content_type=payload.content_type,
            file_type=payload.file_type,
            expected_size=payload.expected_size,
            expected_checksum=payload.expected_checksum,
            expires_seconds=payload.expires_seconds,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return ok(
            {
                "file": _file_read(result.file_object),
                "upload_url": result.upload_url,
                "expires_seconds": result.expires_seconds,
            }
        )


@router.post("/multipart", response_model=Envelope[MultipartUploadInitiatedRead])
async def initiate_multipart_upload(
    request: Request,
    payload: MultipartUploadInitiateRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        result = await (
            await _service(request, session, tenant_id=context.tenant_id)
        ).initiate_multipart_upload(
            tenant_id=context.tenant_id,
            owner_type=payload.owner_type,
            owner_id=payload.owner_id,
            file_name=payload.file_name,
            content_type=payload.content_type,
            file_type=payload.file_type,
            expected_size=payload.expected_size,
            expected_checksum=payload.expected_checksum,
            part_count=payload.part_count,
            expires_seconds=payload.expires_seconds,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return ok(
            {
                "file": _file_read(result.file_object),
                "upload_id": result.upload_id,
                "parts": [
                    {
                        "part_number": part.part_number,
                        "upload_url": part.upload_url,
                    }
                    for part in result.parts
                ],
                "expires_seconds": result.expires_seconds,
            }
        )


@router.post("/multipart/{file_id}/complete", response_model=Envelope[FileObjectRead])
async def complete_multipart_upload(
    request: Request,
    file_id: str,
    payload: MultipartUploadCompleteRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        file_object = await (
            await _service(request, session, tenant_id=context.tenant_id)
        ).complete_multipart_upload(
            file_id=file_id,
            tenant_id=context.tenant_id,
            upload_id=payload.upload_id,
            parts=tuple(
                MultipartUploadPart(part_number=part.part_number, etag=part.etag)
                for part in payload.parts
            ),
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return ok(_file_read(file_object))


@read_router.get("", response_model=ListEnvelope[FileObjectRead])
async def list_files(
    request: Request,
    owner_type: str,
    owner_id: str,
    status: str = "available",
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        files = await (
            await _service(request, session, tenant_id=context.tenant_id)
        ).list_files(
            tenant_id=context.tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            status=status,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return ok_list(
            [_file_read(file_object) for file_object in files],
            Pagination(
                total=len(files),
                page=1,
                page_size=max(len(files), 1),
                has_next=False,
            ),
        )


@read_router.get("/{file_id}", response_model=Envelope[FileObjectRead])
async def get_file_detail(
    request: Request,
    file_id: str,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        file_object = await (
            await _service(request, session, tenant_id=context.tenant_id)
        ).get_file_object(
            file_id=file_id,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return ok(_file_read(file_object))


@read_router.get("/{file_id}/download", response_model=Envelope[FileDownloadRead])
async def download_file(
    request: Request,
    file_id: str,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        service = await _service(request, session, tenant_id=context.tenant_id)
        file_object = await service.get_file_object(
            file_id=file_id,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        download = await service.download_bytes(
            file_id=file_id,
            tenant_id=context.tenant_id,
            owner_type=file_object.owner_type,
            owner_id=file_object.owner_id,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return ok(
            {
                "file_id": download.file_id,
                "file_name": download.file_name,
                "content_type": download.content_type,
                "checksum": download.checksum,
                "size": download.size,
                "content_base64": base64.b64encode(download.data).decode("ascii"),
            }
        )


@read_router.get("/{file_id}/content", response_class=StreamingResponse)
async def stream_file_content(
    request: Request,
    file_id: str,
) -> StreamingResponse:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        service = await _service(request, session, tenant_id=context.tenant_id)
        file_object = await service.get_file_object(
            file_id=file_id,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        download = await service.download_bytes(
            file_id=file_id,
            tenant_id=context.tenant_id,
            owner_type=file_object.owner_type,
            owner_id=file_object.owner_id,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return StreamingResponse(
            BytesIO(download.data),
            media_type=download.content_type,
            headers={
                "Content-Disposition": _content_disposition(download.file_name),
                "Content-Length": str(download.size),
                "X-Content-Checksum": download.checksum,
            },
        )


@read_router.post("/{file_id}/presigned-download", response_model=Envelope[PresignedDownloadRead])
async def create_presigned_download(
    request: Request,
    file_id: str,
    payload: PresignedDownloadRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        result = await (
            await _service(request, session, tenant_id=context.tenant_id)
        ).create_presigned_download(
            file_id=file_id,
            tenant_id=context.tenant_id,
            expires_seconds=payload.expires_seconds,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return ok(
            {
                "file": _file_read(result.file_object),
                "download_url": result.download_url,
                "expires_seconds": result.expires_seconds,
            }
        )


@delete_router.delete("/{file_id}", response_model=Envelope[FileDeleteRead])
async def delete_file(
    request: Request,
    file_id: str,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        file_object = await session.get(FileObject, file_id)
        if file_object is None:
            raise AppError("NOT_FOUND", f"FileObject {file_id!r} not found", status_code=404)
        await (
            await _service(request, session, tenant_id=context.tenant_id)
        ).delete_file(
            file_id=file_id,
            tenant_id=context.tenant_id,
            owner_type=file_object.owner_type,
            owner_id=file_object.owner_id,
            user_id=context.user_id,
            authorization=AuthorizationService(session),
            request_id=context.request_id,
        )
        return ok({"deleted": True})


async def _service(request: Request, session: AsyncSession, *, tenant_id: str) -> FileService:
    return FileService(
        session,
        _storage(request),
        upload_policy=await _upload_policy(request, session, tenant_id=tenant_id),
    )


def _storage(request: Request) -> StorageProvider:
    storage = getattr(request.app.state, "storage_provider", None)
    if storage is None:
        raise AppError("SYSTEM_ERROR", "Storage provider is not configured", status_code=500)
    return storage


def _session_factory(request: Request):
    return request.app.state.session_factory


def _active_session(session: AsyncSession | None) -> AsyncSession:
    if session is None:
        raise AppError("SYSTEM_ERROR", "Database session is not available", status_code=500)
    return session


async def _upload_policy(
    request: Request,
    session: AsyncSession,
    *,
    tenant_id: str,
) -> UploadSecurityPolicy:
    registry = getattr(request.app.state, "setting_registry", None)
    if registry is None or not registry.has_setting(
        module="files",
        key="max_file_size_mb",
    ):
        return DEFAULT_UPLOAD_SECURITY_POLICY
    public_api = importlib.import_module("platform_apps.settings.public_api")
    resolved = await public_api.SettingResolver(session, registry).resolve(
        module="files",
        key="max_file_size_mb",
        tenant_id=tenant_id,
    )
    max_file_size_mb = int(resolved.value)
    return UploadSecurityPolicy(
        max_bytes=max_file_size_mb * 1024 * 1024,
        allowed_content_types=DEFAULT_UPLOAD_SECURITY_POLICY.allowed_content_types,
    )


def _request_context():
    context = get_current_context()
    if context is None or not context.user_id:
        raise AppError("AUTH_INVALID_TOKEN", "Authenticated user is required", status_code=401)
    if not context.tenant_id:
        raise AppError("TENANT_ACCESS_DENIED", "Tenant context is required", status_code=403)
    return context


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeError) as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "content_base64 must be valid base64",
            status_code=400,
        ) from exc


def _file_read(file_object: FileObject) -> dict[str, object]:
    return {
        "id": file_object.id,
        "tenant_id": file_object.tenant_id,
        "owner_type": file_object.owner_type,
        "owner_id": file_object.owner_id,
        "bucket": file_object.bucket,
        "object_key": file_object.object_key,
        "file_name": file_object.file_name,
        "content_type": file_object.content_type,
        "size": file_object.size,
        "checksum": file_object.checksum,
        "file_type": file_object.file_type,
        "status": file_object.status,
        "version": file_object.version,
    }


def _content_disposition(file_name: str) -> str:
    fallback = "".join(
        char if 32 <= ord(char) < 127 and char not in {'"', "\\"} else "_"
        for char in file_name
    )
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(file_name)}'
