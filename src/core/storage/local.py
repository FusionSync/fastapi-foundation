from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from core.exceptions import AppError
from core.storage.provider import MultipartUpload, MultipartUploadRequest, StoredObject


class LocalStorageProvider:
    def __init__(self, root: Path | str, *, bucket: str = "local-files") -> None:
        self.root = Path(root).resolve()
        self.bucket = bucket
        self.root.mkdir(parents=True, exist_ok=True)

    async def put_file(self, object_key: str, data: bytes) -> StoredObject:
        path = self._path_for_key(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(
            bucket=self.bucket,
            object_key=object_key,
            size=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
        )

    async def get_file(self, object_key: str) -> bytes:
        path = self._path_for_key(object_key)
        if not path.is_file():
            raise AppError("NOT_FOUND", "storage object not found", status_code=404)
        return path.read_bytes()

    async def delete_file(self, object_key: str) -> None:
        path = self._path_for_key(object_key)
        if path.exists():
            path.unlink()

    async def exists(self, object_key: str) -> bool:
        return self._path_for_key(object_key).is_file()

    async def generate_download_url(self, object_key: str, *, expires_seconds: int = 300) -> str:
        self._path_for_key(object_key)
        self._validate_expiry(expires_seconds)
        return (
            f"local://{self.bucket}/{quote(object_key)}"
            f"?expires_seconds={expires_seconds}"
        )

    async def generate_upload_url(
        self,
        object_key: str,
        *,
        content_type: str,
        expires_seconds: int = 300,
    ) -> str:
        self._path_for_key(object_key)
        self._validate_content_type(content_type)
        self._validate_expiry(expires_seconds)
        return (
            f"local://{self.bucket}/{quote(object_key)}"
            f"?operation=put&content_type={quote(content_type)}&expires_seconds={expires_seconds}"
        )

    async def create_multipart_upload(
        self,
        object_key: str,
        *,
        content_type: str,
    ) -> MultipartUpload:
        self._path_for_key(object_key)
        self._validate_content_type(content_type)
        return MultipartUpload(object_key=object_key, upload_id=f"local-{uuid4()}")

    async def generate_multipart_part_url(
        self,
        *,
        object_key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int = 300,
    ) -> str:
        self._path_for_key(object_key)
        self._validate_upload_id(upload_id)
        self._validate_part_number(part_number)
        self._validate_expiry(expires_seconds)
        return (
            f"local://{self.bucket}/{quote(object_key)}"
            f"?operation=upload_part&upload_id={quote(upload_id)}"
            f"&part_number={part_number}&expires_seconds={expires_seconds}"
        )

    async def complete_multipart_upload(
        self,
        request: MultipartUploadRequest,
    ) -> StoredObject:
        path = self._path_for_key(request.object_key)
        self._validate_upload_id(request.upload_id)
        if not request.parts:
            raise AppError(
                "VALIDATION_ERROR",
                "multipart upload completion requires at least one part",
                status_code=400,
            )
        size = path.stat().st_size if path.is_file() else 0
        return StoredObject(
            bucket=self.bucket,
            object_key=request.object_key,
            size=size,
            checksum=f"multipart:{request.upload_id}",
        )

    async def abort_multipart_upload(self, *, object_key: str, upload_id: str) -> None:
        self._path_for_key(object_key)
        self._validate_upload_id(upload_id)

    def _path_for_key(self, object_key: str) -> Path:
        if not object_key or object_key.startswith(("/", "\\")) or ".." in Path(object_key).parts:
            raise AppError("VALIDATION_ERROR", "invalid storage object key", status_code=400)
        path = (self.root / object_key).resolve()
        if self.root != path and self.root not in path.parents:
            raise AppError("VALIDATION_ERROR", "invalid storage object key", status_code=400)
        return path

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
