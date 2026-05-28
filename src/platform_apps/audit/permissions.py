from core.permissions import PermissionSpec

PERMISSIONS = [
    PermissionSpec(
        resource="audit_log",
        action="read",
        scope="platform",
        description="Read platform and tenant audit logs.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="audit_log",
        action="export",
        scope="platform",
        description="Export audit logs for compliance review.",
        risk_level="critical",
    ),
]
