from core.apps import SettingSpec

BUILTIN_SETTINGS = [
    SettingSpec(
        module="files",
        key="max_file_size_mb",
        value_type="int",
        default=50,
        scopes=("platform", "tenant"),
        category="file_policy",
        description="Maximum accepted file upload size in MiB.",
        min_value=1,
        max_value=10240,
        risk_level="high",
        cache_ttl_seconds=60,
    ),
    SettingSpec(
        module="auth",
        key="password_min_length",
        value_type="int",
        default=12,
        scopes=("platform",),
        category="security_policy",
        description="Minimum password length for local credentials.",
        min_value=8,
        max_value=128,
        risk_level="high",
    ),
    SettingSpec(
        module="tenancy",
        key="allow_self_service_tenant_create",
        value_type="bool",
        default=False,
        scopes=("platform",),
        category="tenant_policy",
        description="Allow authenticated users to create tenants without operator action.",
        risk_level="critical",
    ),
]
