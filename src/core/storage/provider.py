from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    object_key: str
    size: int
    checksum: str


@dataclass(frozen=True, slots=True)
class MultipartUpload:
    object_key: str
    upload_id: str


@dataclass(frozen=True, slots=True)
class MultipartUploadPart:
    part_number: int
    etag: str


@dataclass(frozen=True, slots=True)
class MultipartUploadRequest:
    object_key: str
    upload_id: str
    parts: Sequence[MultipartUploadPart | Mapping[str, object]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parts",
            tuple(_multipart_part(part) for part in self.parts),
        )


class StorageProvider(Protocol):
    bucket: str

    async def put_file(self, object_key: str, data: bytes) -> StoredObject:
        raise NotImplementedError

    async def get_file(self, object_key: str) -> bytes:
        raise NotImplementedError

    async def delete_file(self, object_key: str) -> None:
        raise NotImplementedError

    async def exists(self, object_key: str) -> bool:
        raise NotImplementedError

    async def generate_download_url(self, object_key: str, *, expires_seconds: int = 300) -> str:
        raise NotImplementedError

    async def generate_upload_url(
        self,
        object_key: str,
        *,
        content_type: str,
        expires_seconds: int = 300,
    ) -> str:
        raise NotImplementedError

    async def create_multipart_upload(
        self,
        object_key: str,
        *,
        content_type: str,
    ) -> MultipartUpload:
        raise NotImplementedError

    async def generate_multipart_part_url(
        self,
        *,
        object_key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int = 300,
    ) -> str:
        raise NotImplementedError

    async def complete_multipart_upload(
        self,
        request: MultipartUploadRequest,
    ) -> StoredObject:
        raise NotImplementedError

    async def abort_multipart_upload(self, *, object_key: str, upload_id: str) -> None:
        raise NotImplementedError


def _multipart_part(part: MultipartUploadPart | Mapping[str, object]) -> MultipartUploadPart:
    if isinstance(part, MultipartUploadPart):
        return part
    return MultipartUploadPart(
        part_number=int(part["part_number"]),
        etag=str(part["etag"]),
    )
