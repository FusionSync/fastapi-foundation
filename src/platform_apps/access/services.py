from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import EventPublisher
from core.exceptions import AppError
from core.permissions import (
    PLATFORM_TENANT_ID,
    AuthorizationDecision,
    PermissionRegistry,
    assert_authorization_decision,
)
from core.permissions.models import RoleGrant, RoleTemplate
from core.permissions.projector import ROLE_GRANT_CHANGED_EVENT


class AccessCatalogService:
    def __init__(self, permission_registry: PermissionRegistry) -> None:
        self.permission_registry = permission_registry

    def list_permissions(self) -> list[dict[str, object]]:
        return [
            permission.to_dict()
            for permission in sorted(
                self.permission_registry.permissions,
                key=lambda item: (
                    item.spec.scope,
                    item.app_label,
                    item.spec.resource,
                    item.spec.action,
                ),
            )
        ]


class PlatformAdminService:
    def __init__(self, session: AsyncSession, events: EventPublisher) -> None:
        self.session = session
        self.events = events

    async def grant_platform_admin(
        self,
        *,
        user_id: str,
        role_template_id: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None = None,
        reason: str | None = None,
    ) -> RoleGrant:
        _assert_platform_admin_mutation_authorized(
            authorization_decision=authorization_decision,
            actor_id=actor_id,
        )
        role_template = await self.session.get(RoleTemplate, role_template_id)
        if role_template is None:
            raise AppError(
                "NOT_FOUND",
                f"RoleTemplate {role_template_id!r} not found",
                status_code=404,
            )
        if role_template.scope != "platform":
            raise AppError(
                "VALIDATION_ERROR",
                "Platform administrator grants require a platform role template",
                status_code=400,
            )

        current = await self._existing_platform_admin_grant(
            user_id=user_id,
            role_template_id=role_template_id,
        )
        if current is not None:
            return current

        grant = RoleGrant(
            id=str(uuid4()),
            tenant_id=PLATFORM_TENANT_ID,
            subject_type="user",
            subject_id=user_id,
            role_template_id=role_template_id,
            policy_version=authorization_decision.policy_version or role_template.version,
        )
        self.session.add(grant)
        await self.session.flush()
        await self.events.publish(
            event_type=ROLE_GRANT_CHANGED_EVENT,
            aggregate_type="role_grant",
            aggregate_id=grant.id,
            tenant_id=PLATFORM_TENANT_ID,
            payload={
                "tenant_id": PLATFORM_TENANT_ID,
                "actor_id": actor_id,
                "request_id": request_id,
                "grant_id": grant.id,
                "subject_type": grant.subject_type,
                "subject_id": grant.subject_id,
                "role_template_id": role_template_id,
                "reason": reason,
            },
        )
        return grant

    async def _existing_platform_admin_grant(
        self,
        *,
        user_id: str,
        role_template_id: str,
    ) -> RoleGrant | None:
        result = await self.session.execute(
            select(RoleGrant)
            .where(RoleGrant.tenant_id == PLATFORM_TENANT_ID)
            .where(RoleGrant.subject_type == "user")
            .where(RoleGrant.subject_id == user_id)
            .where(RoleGrant.role_template_id == role_template_id)
        )
        return result.scalars().first()


def _assert_platform_admin_mutation_authorized(
    *,
    authorization_decision: AuthorizationDecision | None,
    actor_id: str,
) -> None:
    assert_authorization_decision(
        authorization_decision,
        tenant_id=PLATFORM_TENANT_ID,
        actor_id=actor_id,
        resource="access.platform_admin",
        actions={"manage"},
        operation="Platform administrator grant",
        allow_platform=True,
    )


__all__ = [
    "AccessCatalogService",
    "PlatformAdminService",
]
