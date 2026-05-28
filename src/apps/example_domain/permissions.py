from core.permissions import PermissionSpec

PERMISSIONS = [
    PermissionSpec(
        resource="example",
        action="read",
        scope="tenant",
        description="Read example domain records",
    ),
    PermissionSpec(
        resource="example",
        action="write",
        scope="tenant",
        description="Write example domain records",
    ),
]
