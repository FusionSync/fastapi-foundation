from __future__ import annotations

from pydantic import Field

from core.base import Schema


class FileObjectRead(Schema):
    id: str
    tenant_id: str
    owner_type: str
    owner_id: str
    bucket: str
    object_key: str
    file_name: str
    content_type: str
    size: int
    checksum: str
    file_type: str
    status: str
    version: int


class BatchFileContentUploadRequest(Schema):
    file_name: str
    content_type: str
    file_type: str
    content_base64: str
    expected_checksum: str | None = None


class BatchFileUploadRequest(Schema):
    owner_type: str
    owner_id: str
    files: list[BatchFileContentUploadRequest] = Field(min_length=1)


class BatchFileUploadRead(Schema):
    files: list[FileObjectRead]


class PresignedUploadRequest(Schema):
    owner_type: str
    owner_id: str
    file_name: str
    content_type: str
    file_type: str
    expected_size: int = Field(gt=0)
    expires_seconds: int = Field(default=300, gt=0)
    expected_checksum: str | None = None


class PresignedUploadRead(Schema):
    file: FileObjectRead
    upload_url: str
    expires_seconds: int


class FileDownloadRead(Schema):
    file_id: str
    file_name: str
    content_type: str
    checksum: str
    size: int
    content_base64: str


class PresignedDownloadRequest(Schema):
    expires_seconds: int = Field(default=300, gt=0)


class PresignedDownloadRead(Schema):
    file: FileObjectRead
    download_url: str
    expires_seconds: int


class FileDeleteRead(Schema):
    deleted: bool


class MultipartUploadInitiateRequest(Schema):
    owner_type: str
    owner_id: str
    file_name: str
    content_type: str
    file_type: str
    expected_size: int = Field(gt=0)
    part_count: int = Field(gt=0)
    expires_seconds: int = Field(default=300, gt=0)
    expected_checksum: str | None = None


class MultipartPartUploadRead(Schema):
    part_number: int
    upload_url: str


class MultipartUploadInitiatedRead(Schema):
    file: FileObjectRead
    upload_id: str
    parts: list[MultipartPartUploadRead]
    expires_seconds: int


class MultipartUploadPartComplete(Schema):
    part_number: int = Field(gt=0)
    etag: str


class MultipartUploadCompleteRequest(Schema):
    upload_id: str
    parts: list[MultipartUploadPartComplete] = Field(min_length=1)
