from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    object_key: str
    size: int
    checksum: str


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
