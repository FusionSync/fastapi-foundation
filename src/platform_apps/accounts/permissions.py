from core.permissions import PermissionSpec

PERMISSIONS = [
    PermissionSpec(
        resource="user",
        action="manage",
        scope="platform",
        description="Create, disable, and manage platform users.",
        risk_level="critical",
    ),
    PermissionSpec(
        resource="session",
        action="revoke",
        scope="platform",
        description="Revoke user or tenant sessions.",
        risk_level="high",
    ),
]
