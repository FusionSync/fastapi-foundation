from __future__ import annotations


def file_object_key(*, tenant_id: str, file_id: str) -> str:
    return f"tenants/{tenant_id}/files/{file_id}/original.bin"


def resource_object_key(
    *,
    tenant_id: str,
    resource_type: str,
    resource_id: str,
    file_id: str,
) -> str:
    return f"tenants/{tenant_id}/resources/{resource_type}/{resource_id}/{file_id}.bin"
