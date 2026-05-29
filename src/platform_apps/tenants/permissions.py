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
    PermissionSpec(
        resource="tenant_member",
        action="read",
        scope="tenant",
        description="Read tenant membership.",
        risk_level="normal",
    ),
    PermissionSpec(
        resource="tenant_member",
        action="manage",
        scope="tenant",
        description="Manage tenant membership.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="tenant_invitation",
        action="invite",
        scope="tenant",
        description="Invite a user into a tenant.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="tenant_invitation",
        action="revoke",
        scope="tenant",
        description="Revoke a pending tenant invitation.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="tenant_invitation",
        action="manage",
        scope="tenant",
        description="Manage tenant invitations.",
        risk_level="high",
    ),
]
