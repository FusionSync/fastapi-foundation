import pytest

from core.storage import (
    MultipartUploadPart,
    MultipartUploadRequest,
    S3StorageProvider,
)


@pytest.mark.asyncio
async def test_s3_storage_provider_generates_presigned_upload_and_multipart_urls() -> None:
    client = FakeS3Client()
    storage = S3StorageProvider(client, bucket="foundation-files")

    upload_url = await storage.generate_upload_url(
        "tenants/tenant-a/files/file-1/original.bin",
        content_type="application/pdf",
        expires_seconds=600,
    )
    upload = await storage.create_multipart_upload(
        "tenants/tenant-a/files/file-2/original.bin",
        content_type="application/pdf",
    )
    part_url = await storage.generate_multipart_part_url(
        object_key=upload.object_key,
        upload_id=upload.upload_id,
        part_number=2,
        expires_seconds=600,
    )
    completed = await storage.complete_multipart_upload(
        MultipartUploadRequest(
            object_key=upload.object_key,
            upload_id=upload.upload_id,
            parts=[
                MultipartUploadPart(part_number=1, etag="etag-1"),
                MultipartUploadPart(part_number=2, etag="etag-2"),
            ],
        )
    )

    assert upload_url == "presigned:put_object:file-1/original.bin:600"
    assert upload.upload_id == "upload-123"
    assert part_url == "presigned:upload_part:file-2/original.bin:600"
    assert completed.bucket == "foundation-files"
    assert completed.object_key == upload.object_key
    assert completed.size == 0
    assert completed.checksum == "multipart:upload-123"
    assert client.completed_parts == [
        {"ETag": "etag-1", "PartNumber": 1},
        {"ETag": "etag-2", "PartNumber": 2},
    ]


class FakeS3Client:
    def __init__(self) -> None:
        self.completed_parts = []

    def generate_presigned_url(self, client_method, *, Params, ExpiresIn):
        key = Params["Key"].split("/files/", 1)[1]
        return f"presigned:{client_method}:{key}:{ExpiresIn}"

    async def create_multipart_upload(self, **kwargs):
        assert kwargs["Bucket"] == "foundation-files"
        assert kwargs["ContentType"] == "application/pdf"
        return {"UploadId": "upload-123"}

    async def complete_multipart_upload(self, **kwargs):
        self.completed_parts = kwargs["MultipartUpload"]["Parts"]
        return {"ETag": "etag-complete"}

    async def abort_multipart_upload(self, **_kwargs):
        return None

    async def put_object(self, **_kwargs):
        return None

    async def get_object(self, **_kwargs):
        return {"Body": b""}

    async def delete_object(self, **_kwargs):
        return None

    async def head_object(self, **_kwargs):
        return {"ContentLength": 0, "ETag": '"etag-complete"'}
