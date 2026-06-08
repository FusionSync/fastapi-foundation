from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.apps.bootstrap import bootstrap_app
from core.auth import CurrentUser as AuthCurrentUser
from core.config import Settings
from core.context import RequestContext
from core.tenancy import TenantMembership
from core.tenancy.models import Tenant, TenantMember
from core.tenancy.resolver import CurrentUser as TenancyCurrentUser


@dataclass(frozen=True, slots=True)
class BusinessAppTestFixture:
    label: str
    module_path: str
    target_root: Path
    target_dir: Path
    files: tuple[str, ...]
    settings: Settings
    check_app_command: str

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "module_path": self.module_path,
            "target_root": str(self.target_root),
            "target_dir": str(self.target_dir),
            "files": list(self.files),
            "check_app_command": self.check_app_command,
            "settings": {"installed_apps": list(self.settings.installed_apps)},
        }


@dataclass(frozen=True, slots=True)
class TenantUserTestFixture:
    tenant: Tenant
    member: TenantMember
    auth_user: AuthCurrentUser
    tenancy_user: TenancyCurrentUser
    request_context: RequestContext


def create_business_app_fixture(
    label: str,
    *,
    target_root: str | Path,
    package: str = "test_apps",
) -> BusinessAppTestFixture:
    result = bootstrap_app(label, target_root=target_root, package=package)
    settings = Settings(installed_apps=[result.module_path])
    return BusinessAppTestFixture(
        label=result.label,
        module_path=result.module_path,
        target_root=result.target_root,
        target_dir=result.target_dir,
        files=tuple(result.relative_files),
        settings=settings,
        check_app_command=f"core check-app {result.module_path} --json",
    )


def create_tenant_user_fixture(
    *,
    tenant_id: str = "tenant-a",
    user_id: str = "user-1",
    email: str = "user@example.com",
    display_name: str = "Test User",
    tenant_status: str = "active",
    member_status: str = "active",
    deployment_mode: str = "local",
    auth_provider: str = "local",
    session_id: str = "session-1",
    request_id: str = "req_test",
) -> TenantUserTestFixture:
    tenant = Tenant(
        id=tenant_id,
        name=tenant_id,
        code=tenant_id,
        status=tenant_status,
        deployment_mode=deployment_mode,
    )
    member = TenantMember(
        tenant_id=tenant_id,
        user_id=user_id,
        status=member_status,
    )
    auth_user = AuthCurrentUser(
        id=user_id,
        email=email,
        display_name=display_name,
        auth_provider=auth_provider,
        session_id=session_id,
        token_version=1,
        tenant_id=tenant_id,
    )
    tenancy_user = TenancyCurrentUser(
        user_id=user_id,
        default_tenant_id=tenant_id,
        memberships=(
            TenantMembership(
                tenant_id=tenant_id,
                active=member_status == "active",
            ),
        ),
    )
    request_context = RequestContext(
        request_id=request_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    return TenantUserTestFixture(
        tenant=tenant,
        member=member,
        auth_user=auth_user,
        tenancy_user=tenancy_user,
        request_context=request_context,
    )
