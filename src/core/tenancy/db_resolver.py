from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.schemas import CurrentUser as AuthenticatedUser
from core.exceptions import AppError
from core.tenancy.lifecycle import TenantLifecyclePolicy, TenantOperation
from core.tenancy.models import Tenant, TenantMember
from core.tenancy.resolver import (
    CurrentUser,
    TenantMembership,
    TenantRecord,
    resolve_current_tenant,
)


class DatabaseTenantContextResolver:
    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: TenantLifecyclePolicy | None = None,
    ) -> None:
        self.session = session
        self.policy = policy

    async def resolve(
        self,
        *,
        current_user: AuthenticatedUser,
        token_tenant_id: str | None = None,
        header_tenant_id: str | None = None,
        operation: TenantOperation = "read",
        allow_header_tenant_id: bool = False,
    ) -> str:
        resolved_token_tenant_id = token_tenant_id or current_user.tenant_id
        selected_tenant_id = _select_tenant_id(
            token_tenant_id=resolved_token_tenant_id,
            header_tenant_id=header_tenant_id,
            default_tenant_id=current_user.tenant_id,
            allow_header_tenant_id=allow_header_tenant_id,
        )
        tenant_record = await self._tenant_record(selected_tenant_id)
        tenant_user = CurrentUser(
            user_id=current_user.id,
            default_tenant_id=current_user.tenant_id,
            memberships=await self._memberships(current_user.id),
        )
        return resolve_current_tenant(
            current_user=tenant_user,
            token_tenant_id=resolved_token_tenant_id,
            header_tenant_id=header_tenant_id,
            tenant=tenant_record,
            operation=operation,
            policy=self.policy,
            allow_header_tenant_id=allow_header_tenant_id,
        )

    async def _tenant_record(self, tenant_id: str | None) -> TenantRecord | None:
        if tenant_id is None:
            return None
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            return None
        return TenantRecord(
            tenant_id=tenant.id,
            status=tenant.status,  # type: ignore[arg-type]
        )

    async def _memberships(self, user_id: str) -> tuple[TenantMembership, ...]:
        result = await self.session.execute(
            select(TenantMember).where(TenantMember.user_id == user_id)
        )
        return tuple(
            TenantMembership(
                tenant_id=membership.tenant_id,
                active=membership.status == "active",
            )
            for membership in result.scalars().all()
        )


def _select_tenant_id(
    *,
    token_tenant_id: str | None,
    header_tenant_id: str | None,
    default_tenant_id: str | None,
    allow_header_tenant_id: bool,
) -> str | None:
    if header_tenant_id and not allow_header_tenant_id:
        raise AppError(
            "TENANT_CONTEXT_CONFLICT",
            "Header tenant is not allowed for this request",
            status_code=403,
            details={"reason": "header_tenant_not_allowed"},
        )
    if token_tenant_id and header_tenant_id and token_tenant_id != header_tenant_id:
        raise AppError(
            "TENANT_CONTEXT_CONFLICT",
            "Header tenant conflicts with token tenant",
            status_code=403,
            details={"token_tenant_id": token_tenant_id, "header_tenant_id": header_tenant_id},
        )
    return token_tenant_id or header_tenant_id or default_tenant_id
