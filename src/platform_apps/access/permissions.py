from core.permissions import PermissionSpec

PERMISSIONS = [
    PermissionSpec(
        resource="access.permission",
        action="read",
        scope="platform",
        description="Read the registered platform and tenant permission catalog.",
        risk_level="normal",
    ),
    PermissionSpec(
        resource="access.role_template",
        action="read",
        scope="platform",
        description="Read platform role templates and IAM assignment options.",
        risk_level="normal",
    ),
    PermissionSpec(
        resource="access.role_template",
        action="manage",
        scope="platform",
        description="Create and update platform role templates.",
        risk_level="critical",
    ),
    PermissionSpec(
        resource="access.platform_admin",
        action="read",
        scope="platform",
        description="Read platform administrator grants.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="access.platform_admin",
        action="manage",
        scope="platform",
        description="Grant platform administrator access in the platform domain.",
        risk_level="critical",
    ),
    PermissionSpec(
        resource="role_grant",
        action="read",
        scope="tenant",
        description="Read tenant role grants.",
        risk_level="normal",
    ),
    PermissionSpec(
        resource="role_grant",
        action="grant",
        scope="tenant",
        description="Grant tenant roles.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="role_grant",
        action="revoke",
        scope="tenant",
        description="Revoke tenant role grants.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="access.effective",
        action="read",
        scope="platform",
        description="Read a subject's effective projected permissions.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="access.reconcile",
        action="manage",
        scope="platform",
        description="Validate and repair IAM role-grant projections.",
        risk_level="critical",
    ),
]
