from __future__ import annotations

import hashlib
import inspect
from pathlib import PurePosixPath
from typing import Any, Protocol

from core.exceptions import AppError
from core.storage.provider import StoredObject


class S3StorageClient(Protocol):
    async def put_object(self, **kwargs: Any) -> Any: ...

    async def get_object(self, **kwargs: Any) -> Any: ...

    async def delete_object(self, **kwargs: Any) -> Any: ...

    async def head_object(self, **kwargs: Any) -> Any: ...

    def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: dict[str, str],
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
                "download URL expiry must be greater than zero",
                status_code=400,
            )
