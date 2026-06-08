from core.permissions import PermissionSpec

PERMISSIONS = [
    PermissionSpec(
        resource="settings.definition",
        action="read",
        scope="platform",
        description="Read registered runtime setting definitions.",
        risk_level="normal",
    ),
    PermissionSpec(
        resource="settings.value",
        action="read",
        scope="platform",
        description="Read resolved platform and tenant setting values.",
        risk_level="high",
    ),
    PermissionSpec(
        resource="settings.value",
        action="manage",
        scope="platform",
        description="Manage platform setting overrides.",
        risk_level="critical",
    ),
    PermissionSpec(
        resource="settings.tenant",
        action="read",
        scope="tenant",
        description="Read tenant setting overrides.",
        risk_level="normal",
    ),
    PermissionSpec(
        resource="settings.tenant",
        action="manage",
        scope="tenant",
        description="Manage tenant setting overrides.",
        risk_level="high",
    ),
]
