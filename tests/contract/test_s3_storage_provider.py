from typing import Any

import pytest

from core.exceptions import AppError
from core.storage import S3StorageProvider


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.deleted: list[tuple[str, str]] = []

    async def put_object(self, **kwargs: Any) -> dict[str, object]:
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
        return {}

    async def get_object(self, **kwargs: Any) -> dict[str, object]:
        try:
            data = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        except KeyError as exc:
            raise KeyError("NoSuchKey") from exc
        return {"Body": _Body(data)}

    async def delete_object(self, **kwargs: Any) -> dict[str, object]:
        self.deleted.append((kwargs["Bucket"], kwargs["Key"]))
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)
        return {}

    async def head_object(self, **kwargs: Any) -> dict[str, object]:
        if (kwargs["Bucket"], kwargs["Key"]) not in self.objects:
            raise KeyError("NoSuchKey")
        return {}

    async def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: dict[str, str],
        ExpiresIn: int,
    ) -> str:
        return (
            f"https://storage.example/{Params['Bucket']}/{Params['Key']}"
            f"?method={client_method}&expires={ExpiresIn}"
        )


@pytest.mark.asyncio
async def test_s3_storage_provider_put_get_delete_and_presign() -> None:
    client = FakeS3Client()
    storage = S3StorageProvider(client, bucket="tenant-files")
    object_key = "tenants/tenant-a/files/file-1/original.bin"

    stored = await storage.put_file(object_key, b"file-bytes")
    data = await storage.get_file(object_key)
    url = await storage.generate_download_url(object_key, expires_seconds=60)
    exists_before_delete = await storage.exists(object_key)
    await storage.delete_file(object_key)

    assert stored.bucket == "tenant-files"
    assert stored.object_key == object_key
    assert stored.size == len(b"file-bytes")
    assert stored.checksum
    assert data == b"file-bytes"
    assert exists_before_delete is True
    assert await storage.exists(object_key) is False
    assert client.deleted == [("tenant-files", object_key)]
    assert url == (
        "https://storage.example/tenant-files/tenants/tenant-a/files/file-1/original.bin"
        "?method=get_object&expires=60"
    )


@pytest.mark.asyncio
async def test_s3_storage_provider_rejects_unsafe_object_keys() -> None:
    storage = S3StorageProvider(FakeS3Client(), bucket="tenant-files")

    with pytest.raises(AppError) as absolute:
        await storage.put_file("/tenant-a/file.bin", b"file")
    with pytest.raises(AppError) as traversal:
        await storage.generate_download_url("tenants/tenant-a/../file.bin")

    assert absolute.value.code == "VALIDATION_ERROR"
    assert traversal.value.code == "VALIDATION_ERROR"
