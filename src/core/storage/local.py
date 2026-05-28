from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

from core.exceptions import AppError
from core.storage.provider import StoredObject


class LocalStorageProvider:
    def __init__(self, *, root: Path | str, bucket: str = "local-files") -> None:
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
        return (
            f"local://{self.bucket}/{quote(object_key)}"
            f"?expires_seconds={expires_seconds}"
        )

    def _path_for_key(self, object_key: str) -> Path:
        if not object_key or object_key.startswith(("/", "\\")) or ".." in Path(object_key).parts:
            raise AppError("VALIDATION_ERROR", "invalid storage object key", status_code=400)
        path = (self.root / object_key).resolve()
        if self.root != path and self.root not in path.parents:
            raise AppError("VALIDATION_ERROR", "invalid storage object key", status_code=400)
        return path
