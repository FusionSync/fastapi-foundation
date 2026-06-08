from __future__ import annotations

import hashlib
import inspect
from pathlib import PurePosixPath
from typing import Any, Protocol

from core.exceptions import AppError
from core.storage.provider import MultipartUpload, MultipartUploadRequest, StoredObject


class S3StorageClient(Protocol):
    async def put_object(self, **kwargs: Any) -> Any: ...

    async def get_object(self, **kwargs: Any) -> Any: ...

    async def delete_object(self, **kwargs: Any) -> Any: ...

    async def head_object(self, **kwargs: Any) -> Any: ...

    async def create_multipart_upload(self, **kwargs: Any) -> Any: ...

    async def complete_multipart_upload(self, **kwargs: Any) -> Any: ...

    async def abort_multipart_upload(self, **kwargs: Any) -> Any: ...

    def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: dict[str, object],
        ExpiresIn: int,
    ) -> Any: ...


class S3StorageProvider:
    def __init__(self, client: S3StorageClient, *, bucket: str) -> None:
        if not bucket.strip():
            raise AppError("VALIDATION_ERROR", "storage bucket is required", status_code=400)
        self.client = client
        self.bucket = bucket

    async def put_file(self, object_key: str, data: bytes) -> StoredObject:
        self._validate_object_key(object_key)
        await self.client.put_object(Bucket=self.bucket, Key=object_key, Body=data)
        return StoredObject(
            bucket=self.bucket,
            object_key=object_key,
            size=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
        )

    async def get_file(self, object_key: str) -> bytes:
        self._validate_object_key(object_key)
        try:
            response = await self.client.get_object(Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            raise AppError("NOT_FOUND", "storage object not found", status_code=404) from exc
        body = response["Body"]
        data = body.read()
        if inspect.isawaitable(data):
            data = await data
        return bytes(data)

    async def delete_file(self, object_key: str) -> None:
        self._validate_object_key(object_key)
        await self.client.delete_object(Bucket=self.bucket, Key=object_key)

    async def exists(self, object_key: str) -> bool:
        self._validate_object_key(object_key)
        try:
            await self.client.head_object(Bucket=self.bucket, Key=object_key)
        except Exception:
            return False
        return True

    async def generate_download_url(self, object_key: str, *, expires_seconds: int = 300) -> str:
        self._validate_object_key(object_key)
        self._validate_expiry(expires_seconds)
        value = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expires_seconds,
        )
        if inspect.isawaitable(value):
            return str(await value)
        return str(value)

    async def generate_upload_url(
        self,
        object_key: str,
        *,
        content_type: str,
        expires_seconds: int = 300,
    ) -> str:
        self._validate_object_key(object_key)
        self._validate_content_type(content_type)
        self._validate_expiry(expires_seconds)
        value = self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_seconds,
        )
        if inspect.isawaitable(value):
            return str(await value)
        return str(value)

    async def create_multipart_upload(
        self,
        object_key: str,
        *,
        content_type: str,
    ) -> MultipartUpload:
        self._validate_object_key(object_key)
        self._validate_content_type(content_type)
        response = await self.client.create_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            ContentType=content_type,
        )
        upload_id = str(response.get("UploadId") or "")
        if not upload_id:
            raise AppError(
                "STORAGE_ERROR",
                "S3 multipart upload did not return an upload id",
                status_code=502,
            )
        return MultipartUpload(object_key=object_key, upload_id=upload_id)

    async def generate_multipart_part_url(
        self,
        *,
        object_key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int = 300,
    ) -> str:
        self._validate_object_key(object_key)
        self._validate_upload_id(upload_id)
        self._validate_part_number(part_number)
        self._validate_expiry(expires_seconds)
        value = self.client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=expires_seconds,
        )
        if inspect.isawaitable(value):
            return str(await value)
        return str(value)

    async def complete_multipart_upload(
        self,
        request: MultipartUploadRequest,
    ) -> StoredObject:
        self._validate_object_key(request.object_key)
        self._validate_upload_id(request.upload_id)
        parts = [
            {"ETag": part.etag, "PartNumber": part.part_number}
            for part in sorted(request.parts, key=lambda item: item.part_number)
        ]
        if not parts:
            raise AppError(
                "VALIDATION_ERROR",
                "multipart upload completion requires at least one part",
                status_code=400,
            )
        for part in request.parts:
            self._validate_part_number(part.part_number)
            if not part.etag.strip():
                raise AppError(
                    "VALIDATION_ERROR",
                    "multipart part etag is required",
                    status_code=400,
                )
        await self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=request.object_key,
            UploadId=request.upload_id,
            MultipartUpload={"Parts": parts},
        )
        head = await self.client.head_object(Bucket=self.bucket, Key=request.object_key)
        return StoredObject(
            bucket=self.bucket,
            object_key=request.object_key,
            size=int(head.get("ContentLength") or 0),
            checksum=f"multipart:{request.upload_id}",
        )

    async def abort_multipart_upload(self, *, object_key: str, upload_id: str) -> None:
        self._validate_object_key(object_key)
        self._validate_upload_id(upload_id)
        await self.client.abort_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            UploadId=upload_id,
        )

    def _validate_object_key(self, object_key: str) -> None:
        path = PurePosixPath(object_key)
        if (
            not object_key.strip()
            or object_key.startswith(("/", "\\"))
            or path.is_absolute()
            or ".." in path.parts
        ):
            raise AppError("VALIDATION_ERROR", "invalid storage object key", status_code=400)

    def _validate_expiry(self, expires_seconds: int) -> None:
        if expires_seconds <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "URL expiry must be greater than zero",
                status_code=400,
            )

    def _validate_content_type(self, content_type: str) -> None:
        if not content_type.strip():
            raise AppError("VALIDATION_ERROR", "content_type is required", status_code=400)

    def _validate_upload_id(self, upload_id: str) -> None:
        if not upload_id.strip():
            raise AppError("VALIDATION_ERROR", "multipart upload_id is required", status_code=400)

    def _validate_part_number(self, part_number: int) -> None:
        if part_number <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "multipart part_number must be greater than zero",
                status_code=400,
            )
