from __future__ import annotations

from pydantic import Field

from core.base import BaseSchema


class FileObjectRead(BaseSchema):
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


class BatchFileContentUploadRequest(BaseSchema):
    file_name: str
    content_type: str
    file_type: str
    content_base64: str
    expected_checksum: str | None = None


class BatchFileUploadRequest(BaseSchema):
    owner_type: str
    owner_id: str
    files: list[BatchFileContentUploadRequest] = Field(min_length=1)


class BatchFileUploadRead(BaseSchema):
    files: list[FileObjectRead]


class PresignedUploadRequest(BaseSchema):
    owner_type: str
    owner_id: str
    file_name: str
    content_type: str
    file_type: str
    expected_size: int = Field(gt=0)
    expires_seconds: int = Field(default=300, gt=0)
    expected_checksum: str | None = None


class PresignedUploadRead(BaseSchema):
    file: FileObjectRead
    upload_url: str
    expires_seconds: int


class MultipartUploadInitiateRequest(BaseSchema):
    owner_type: str
    owner_id: str
    file_name: str
    content_type: str
    file_type: str
    expected_size: int = Field(gt=0)
    part_count: int = Field(gt=0)
    expires_seconds: int = Field(default=300, gt=0)
    expected_checksum: str | None = None


class MultipartPartUploadRead(BaseSchema):
    part_number: int
    upload_url: str


class MultipartUploadInitiatedRead(BaseSchema):
    file: FileObjectRead
    upload_id: str
    parts: list[MultipartPartUploadRead]
    expires_seconds: int


class MultipartUploadPartComplete(BaseSchema):
    part_number: int = Field(gt=0)
    etag: str


class MultipartUploadCompleteRequest(BaseSchema):
    upload_id: str
    parts: list[MultipartUploadPartComplete] = Field(min_length=1)
