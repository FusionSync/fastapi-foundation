from core.permissions import PermissionSpec

PERMISSIONS = [
    PermissionSpec(
        resource="tenant",
        action="manage",
        scope="platform",
        description="Manage tenant lifecycle and operational controls.",
        risk_level="critical",
    ),
    PermissionSpec(
        resource="tenant",
        action="provision",
        scope="platform",
        description="Provision new tenants.",
        risk_level="critical",
    ),
    PermissionSpec(
        resource="tenant",
        action="suspend",
        scope="platform",
        description="Suspend active tenants.",
        risk_level="critical",
    ),
    PermissionSpec(
        resource="tenant",
        action="reactivate",
        scope="platform",
        description="Reactivate suspended tenants.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="tenant",
        action="delete",
        scope="platform",
        description="Begin or finish tenant deletion.",
        risk_level="critical",
    ),
]
