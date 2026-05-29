from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.engine import make_url

from core.config.settings import DatabaseTenantFallbackMode, DeploymentMode, Settings

_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_SETTING_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


@dataclass(frozen=True, slots=True)
class DatabaseTenantRlsPolicy:
    table: str
    policy_name: str
    enable_rls: str
    force_rls: str
    using_expression: str
    with_check_expression: str
    create_policy: str

    def to_dict(self) -> dict[str, str]:
        return {
            "table": self.table,
            "policy_name": self.policy_name,
            "enable_rls": self.enable_rls,
            "force_rls": self.force_rls,
            "using_expression": self.using_expression,
            "with_check_expression": self.with_check_expression,
            "create_policy": self.create_policy,
        }


@dataclass(frozen=True, slots=True)
class DatabaseTenantAdvisoryLock:
    scope: str
    statement: str
    key_expression: str

    def to_dict(self) -> dict[str, str]:
        return {
            "scope": self.scope,
            "statement": self.statement,
            "key_expression": self.key_expression,
        }


@dataclass(frozen=True, slots=True)
class DatabaseTenantGuardReport:
    ok: bool
    profile: DeploymentMode
    required: bool
    dialect: str
    strategy: str
    fallback_mode: DatabaseTenantFallbackMode
    setting_name: str
    checks: dict[str, str]
    errors: list[str]
    warnings: list[str]
    rls_policies: tuple[DatabaseTenantRlsPolicy, ...]
    advisory_lock: DatabaseTenantAdvisoryLock | None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "profile": self.profile,
            "required": self.required,
            "dialect": self.dialect,
            "strategy": self.strategy,
            "fallback_mode": self.fallback_mode,
            "setting_name": self.setting_name,
            "checks": dict(self.checks),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "rls_policies": [policy.to_dict() for policy in self.rls_policies],
            "advisory_lock": (
                self.advisory_lock.to_dict()
                if self.advisory_lock is not None
                else None
            ),
        }


def verify_database_tenant_guard(
    settings: Settings,
    *,
    profile: DeploymentMode | None = None,
    tenant_tables: Sequence[str] = (),
) -> DatabaseTenantGuardReport:
    resolved_profile = profile or settings.app.env
    required = resolved_profile == "cloud"
    dialect = _database_dialect(settings.database.url)
    fallback_mode = settings.database.tenant_fallback_mode
    setting_name = settings.database.tenant_fallback_setting_name
    checks: dict[str, str] = {}
    errors: list[str] = []
    warnings: list[str] = []

    if dialect == "postgresql":
        checks["postgresql_dialect"] = "ok"
    elif required:
        checks["postgresql_dialect"] = "error"
        errors.append("cloud profile requires PostgreSQL database URL for tenant guard")
    else:
        checks["postgresql_dialect"] = "not_required"

    if fallback_mode == "session_variable":
        checks["session_variable"] = "ok"
    elif required:
        checks["session_variable"] = "error"
        errors.append(
            "cloud profile requires DATABASE__TENANT_FALLBACK_MODE=session_variable"
        )
    else:
        checks["session_variable"] = "not_enabled"

    if _SETTING_NAME_PATTERN.fullmatch(setting_name):
        checks["setting_name"] = "ok"
    else:
        checks["setting_name"] = "error"
        errors.append("DATABASE__TENANT_FALLBACK_SETTING_NAME is invalid")

    can_plan_database_guard = (
        dialect == "postgresql"
        and fallback_mode == "session_variable"
        and checks["setting_name"] == "ok"
    )
    rls_policies: list[DatabaseTenantRlsPolicy] = []
    for table in tenant_tables:
        if not _IDENTIFIER_PATTERN.fullmatch(table):
            errors.append(f"tenant table name is invalid: {table}")
            continue
        if can_plan_database_guard:
            rls_policies.append(_rls_policy(table=table, setting_name=setting_name))

    if can_plan_database_guard:
        checks["rls_policy_plan"] = "ok"
        checks["advisory_lock_plan"] = "ok"
    elif required:
        checks["rls_policy_plan"] = "error"
        checks["advisory_lock_plan"] = "error"
    else:
        checks["rls_policy_plan"] = "not_available"
        checks["advisory_lock_plan"] = "not_available"

    advisory_lock = (
        _advisory_lock(setting_name=setting_name) if can_plan_database_guard else None
    )
    strategy = "repository_plus_rls" if can_plan_database_guard else "repository"
    if not required and fallback_mode == "disabled":
        warnings.append("database tenant guard is repository-only for this profile")

    return DatabaseTenantGuardReport(
        ok=not errors,
        profile=resolved_profile,
        required=required,
        dialect=dialect,
        strategy=strategy,
        fallback_mode=fallback_mode,
        setting_name=setting_name,
        checks=checks,
        errors=errors,
        warnings=warnings,
        rls_policies=tuple(rls_policies),
        advisory_lock=advisory_lock,
    )


def _database_dialect(url: str) -> str:
    try:
        return make_url(url).get_backend_name()
    except Exception:
        return ""


def _rls_policy(*, table: str, setting_name: str) -> DatabaseTenantRlsPolicy:
    policy_name = f"{table.replace('.', '_')}_tenant_isolation"
    expression = f"tenant_id = current_setting('{setting_name}', true)"
    return DatabaseTenantRlsPolicy(
        table=table,
        policy_name=policy_name,
        enable_rls=f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        force_rls=f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        using_expression=expression,
        with_check_expression=expression,
        create_policy=(
            f"CREATE POLICY {policy_name} ON {table} "
            f"USING ({expression}) WITH CHECK ({expression})"
        ),
    )


def _advisory_lock(*, setting_name: str) -> DatabaseTenantAdvisoryLock:
    key_expression = f"hashtext(current_setting('{setting_name}', true))"
    return DatabaseTenantAdvisoryLock(
        scope="transaction",
        statement=f"SELECT pg_advisory_xact_lock({key_expression})",
        key_expression=key_expression,
    )
