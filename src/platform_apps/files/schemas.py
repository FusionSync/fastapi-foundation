from __future__ import annotations

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
