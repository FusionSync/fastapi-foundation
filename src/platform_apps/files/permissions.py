from core.permissions import PermissionSpec

PERMISSIONS = [
    PermissionSpec(
        resource="file",
        action="upload",
        scope="tenant",
        description="Upload tenant-scoped file objects.",
        risk_level="normal",
    ),
    PermissionSpec(
        resource="file",
        action="download",
        scope="tenant",
        description="Download tenant-scoped file objects.",
        risk_level="normal",
    ),
    PermissionSpec(
        resource="file",
        action="delete",
        scope="tenant",
        description="Soft-delete tenant-scoped file objects.",
        risk_level="high",
    ),
]
