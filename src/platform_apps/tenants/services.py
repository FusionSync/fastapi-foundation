from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditRecorder
from core.events import EventPublisher
from core.exceptions import AppError
from core.permissions import (
    AuthorizationDecision,
    RoleGrantService,
    assert_authorization_decision,
)
from core.permissions.models import RoleTemplate
from core.tenancy import (
    TENANT_MEMBER_ACTIVATED_EVENT,
    Tenant,
    TenantInvitation,
    TenantLifecycleService,
    TenantMember,
    publish_tenant_membership_event,
)

TENANT_INVITATION_ISSUED_EVENT = "tenant.invitation_issued"
TENANT_INVITATION_ACCEPTED_EVENT = "tenant.invitation_accepted"
TENANT_INVITATION_REVOKED_EVENT = "tenant.invitation_revoked"


@dataclass(frozen=True, slots=True)
class TenantInvitationIssue:
    invitation: TenantInvitation
    token: str


class TenantInvitationService:
    def __init__(
        self,
        session: AsyncSession,
        events: EventPublisher,
        *,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.events = events
        self.audit = audit

    async def issue_invitation(
        self,
        *,
        tenant_id: str,
        email: str,
        role_template_id: str | None,
        actor_id: str,
        request_id: str,
        expires_at: datetime,
        authorization_decision: AuthorizationDecision | None = None,
        role_grant_authorization_decision: AuthorizationDecision | None = None,
    ) -> TenantInvitationIssue:
        _assert_invitation_mutation_authorized(
            authorization_decision=authorization_decision,
            tenant_id=tenant_id,
            actor_id=actor_id,
            mutation="invite",
        )
        if role_template_id is not None:
            _assert_role_grant_authorized(
                authorization_decision=role_grant_authorization_decision,
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
            await self._assert_role_template_exists(role_template_id)

        normalized_email = _normalize_email(email)
        expires_at = _ensure_aware(expires_at)
        if expires_at <= datetime.now(UTC):
            raise AppError(
                "VALIDATION_ERROR",
                "Invitation expiry must be in the future",
                status_code=400,
            )
        await self._assert_tenant_accepts_invitations(tenant_id)
        await self._assert_no_active_invitation(tenant_id=tenant_id, email=normalized_email)

        token = token_urlsafe(32)
        invitation = TenantInvitation(
            tenant_id=tenant_id,
            email=normalized_email,
            role_template_id=role_template_id,
            token_hash=_hash_token(token),
            status="pending",
            invited_by_user_id=actor_id,
            role_grant_authorized_by_user_id=(
                role_grant_authorization_decision.user_id
                if role_grant_authorization_decision is not None
                else None
            ),
            role_grant_policy_version=(
                role_grant_authorization_decision.policy_version
                if role_grant_authorization_decision is not None
                else None
            ),
            expires_at=expires_at,
        )
        self.session.add(invitation)
        await self.session.flush()
        await self.events.publish(
            event_type=TENANT_INVITATION_ISSUED_EVENT,
            aggregate_type="tenant_invitation",
            aggregate_id=invitation.id,
            tenant_id=tenant_id,
            payload={
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "request_id": request_id,
                "invitation_id": invitation.id,
                "email": normalized_email,
                "role_template_id": role_template_id,
            },
        )
        if self.audit is not None:
            await self.audit.record(
                action=TENANT_INVITATION_ISSUED_EVENT,
                resource_type="tenant_invitation",
                resource_id=invitation.id,
                result="success",
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=request_id,
                payload={
                    "email": normalized_email,
                    "role_template_id": role_template_id,
                },
            )
        return TenantInvitationIssue(invitation=invitation, token=token)

    async def accept_invitation(
        self,
        *,
        token: str,
        user_id: str,
        email: str,
        actor_id: str,
        request_id: str,
        accepted_at: datetime | None = None,
    ) -> TenantInvitation:
        if actor_id != user_id:
            raise AppError(
                "PERMISSION_DENIED",
                "Invitation accept actor must match user",
                status_code=403,
            )
        resolved_at = accepted_at or datetime.now(UTC)
        invitation = await self._pending_invitation_by_token(token)
        if invitation is None:
            raise AppError("NOT_FOUND", "Tenant invitation not found", status_code=404)
        if _normalize_email(email) != invitation.email:
            raise AppError(
                "VALIDATION_ERROR",
                "Invitation email does not match user email",
                status_code=400,
            )
        if _ensure_aware(invitation.expires_at) <= resolved_at:
            invitation.status = "expired"
            await self.session.flush()
            raise AppError(
                "VALIDATION_ERROR",
                "Tenant invitation has expired",
                status_code=400,
            )

        await self._activate_member(
            tenant_id=invitation.tenant_id,
            user_id=user_id,
            actor_id=actor_id,
            request_id=request_id,
        )
        if invitation.role_template_id is not None:
            await self._grant_initial_role(
                invitation=invitation,
                user_id=user_id,
                request_id=request_id,
            )
        invitation.status = "accepted"
        invitation.accepted_at = resolved_at
        invitation.accepted_by_user_id = user_id
        await self.events.publish(
            event_type=TENANT_INVITATION_ACCEPTED_EVENT,
            aggregate_type="tenant_invitation",
            aggregate_id=invitation.id,
            tenant_id=invitation.tenant_id,
            payload={
                "tenant_id": invitation.tenant_id,
                "actor_id": actor_id,
                "request_id": request_id,
                "invitation_id": invitation.id,
                "user_id": user_id,
                "role_template_id": invitation.role_template_id,
            },
        )
        if self.audit is not None:
            await self.audit.record(
                action=TENANT_INVITATION_ACCEPTED_EVENT,
                resource_type="tenant_invitation",
                resource_id=invitation.id,
                result="success",
                tenant_id=invitation.tenant_id,
                actor_id=actor_id,
                request_id=request_id,
                payload={
                    "user_id": user_id,
                    "role_template_id": invitation.role_template_id,
                },
            )
        await self.session.flush()
        return invitation

    async def revoke_invitation(
        self,
        invitation: TenantInvitation,
        *,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None = None,
        revoked_at: datetime | None = None,
    ) -> TenantInvitation:
        _assert_invitation_mutation_authorized(
            authorization_decision=authorization_decision,
            tenant_id=invitation.tenant_id,
            actor_id=actor_id,
            mutation="revoke",
        )
        if invitation.status != "pending":
            raise AppError(
                "CONFLICT",
                "Only pending invitations can be revoked",
                status_code=409,
            )
        resolved_at = revoked_at or datetime.now(UTC)
        invitation.status = "revoked"
        invitation.revoked_at = resolved_at
        invitation.revoked_by_user_id = actor_id
        await self.events.publish(
            event_type=TENANT_INVITATION_REVOKED_EVENT,
            aggregate_type="tenant_invitation",
            aggregate_id=invitation.id,
            tenant_id=invitation.tenant_id,
            payload={
                "tenant_id": invitation.tenant_id,
                "actor_id": actor_id,
                "request_id": request_id,
                "invitation_id": invitation.id,
            },
        )
        await self.session.flush()
        return invitation

    async def _assert_tenant_accepts_invitations(self, tenant_id: str) -> None:
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            raise AppError("NOT_FOUND", f"Tenant {tenant_id!r} not found", status_code=404)
        if tenant.status != "active":
            raise AppError(
                "TENANT_STATE_FORBIDDEN",
                "Tenant must be active to issue invitations",
                status_code=403,
                details={"tenant_id": tenant_id, "status": tenant.status},
            )

    async def _assert_role_template_exists(self, role_template_id: str) -> None:
        role_template = await self.session.get(RoleTemplate, role_template_id)
        if role_template is None:
            raise AppError(
                "NOT_FOUND",
                f"RoleTemplate {role_template_id!r} not found",
                status_code=404,
            )

    async def _assert_no_active_invitation(self, *, tenant_id: str, email: str) -> None:
        result = await self.session.execute(
            select(TenantInvitation)
            .where(TenantInvitation.tenant_id == tenant_id)
            .where(TenantInvitation.email == email)
            .where(TenantInvitation.status == "pending")
        )
        current = result.scalars().first()
        if current is not None and _ensure_aware(current.expires_at) > datetime.now(UTC):
            raise AppError(
                "CONFLICT",
                "Pending invitation already exists",
                status_code=409,
            )

    async def _pending_invitation_by_token(self, token: str) -> TenantInvitation | None:
        result = await self.session.execute(
            select(TenantInvitation)
            .where(TenantInvitation.token_hash == _hash_token(token))
            .where(TenantInvitation.status == "pending")
        )
        return result.scalars().first()

    async def _activate_member(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        request_id: str,
    ) -> TenantMember:
        result = await self.session.execute(
            select(TenantMember)
            .where(TenantMember.tenant_id == tenant_id)
            .where(TenantMember.user_id == user_id)
        )
        member = result.scalars().first()
        previous_status: str | None
        if member is None:
            previous_status = None
            member = TenantMember(tenant_id=tenant_id, user_id=user_id, status="active")
            self.session.add(member)
        else:
            previous_status = member.status
            member.status = "active"
        await self.session.flush()
        if previous_status != "active":
            await publish_tenant_membership_event(
                self.events,
                TENANT_MEMBER_ACTIVATED_EVENT,
                tenant_id=tenant_id,
                user_id=user_id,
                member_id=member.id,
                status=member.status,
                actor_id=actor_id,
                request_id=request_id,
                extra={
                    "change_type": "created" if previous_status is None else "activated",
                },
            )
        return member

    async def _grant_initial_role(
        self,
        *,
        invitation: TenantInvitation,
        user_id: str,
        request_id: str,
    ) -> None:
        actor_id = invitation.role_grant_authorized_by_user_id or invitation.invited_by_user_id
        decision = AuthorizationDecision(
            allowed=True,
            tenant_id=invitation.tenant_id,
            user_id=actor_id,
            resource="role_grant",
            action="grant",
            reason="tenant_invitation_accepted",
            policy_version=invitation.role_grant_policy_version,
        )
        await RoleGrantService(
            self.session,
            self.events,
            audit=self.audit,
        ).grant_role(
            tenant_id=invitation.tenant_id,
            subject_type="user",
            subject_id=user_id,
            role_template_id=invitation.role_template_id,
            actor_id=actor_id,
            request_id=request_id,
            authorization_decision=decision,
            reason="tenant invitation accepted",
            policy_version=invitation.role_grant_policy_version or 1,
        )


def _assert_invitation_mutation_authorized(
    *,
    authorization_decision: AuthorizationDecision | None,
    tenant_id: str,
    actor_id: str,
    mutation: str,
) -> None:
    assert_authorization_decision(
        authorization_decision,
        tenant_id=tenant_id,
        actor_id=actor_id,
        resource="tenant_invitation",
        actions={"manage", mutation},
        operation="Tenant invitation mutation",
    )


def _assert_role_grant_authorized(
    *,
    authorization_decision: AuthorizationDecision | None,
    tenant_id: str,
    actor_id: str,
) -> None:
    assert_authorization_decision(
        authorization_decision,
        tenant_id=tenant_id,
        actor_id=actor_id,
        resource="role_grant",
        actions={"manage", "grant"},
        operation="Tenant invitation initial role grant",
    )


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise AppError("VALIDATION_ERROR", "Invitation email is invalid", status_code=400)
    return normalized


def _hash_token(token: str) -> str:
    if not token.strip():
        raise AppError("VALIDATION_ERROR", "Invitation token is required", status_code=400)
    return sha256(token.encode("utf-8")).hexdigest()


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "TENANT_INVITATION_ACCEPTED_EVENT",
    "TENANT_INVITATION_ISSUED_EVENT",
    "TENANT_INVITATION_REVOKED_EVENT",
    "TenantInvitationIssue",
    "TenantInvitationService",
    "TenantLifecycleService",
]
