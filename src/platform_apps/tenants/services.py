from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import func, or_, select
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
from platform_apps.tenants.schemas import TenantListQuery, TenantMemberListQuery

TENANT_INVITATION_ISSUED_EVENT = "tenant.invitation_issued"
TENANT_INVITATION_ACCEPTED_EVENT = "tenant.invitation_accepted"
TENANT_INVITATION_REVOKED_EVENT = "tenant.invitation_revoked"


@dataclass(frozen=True, slots=True)
class TenantInvitationIssue:
    invitation: TenantInvitation
    token: str


class TenantQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_tenants(self, query: TenantListQuery) -> tuple[list[Tenant], int]:
        filters = []
        if query.status is not None:
            filters.append(Tenant.status == query.status)
        if query.keyword is not None:
            keyword = f"%{query.keyword.strip().lower()}%"
            filters.append(
                or_(
                    func.lower(Tenant.name).like(keyword),
                    func.lower(Tenant.code).like(keyword),
                )
            )
        total = await self.session.scalar(
            select(func.count()).select_from(Tenant).where(*filters)
        )
        statement = (
            select(Tenant)
            .where(*filters)
            .order_by(*_tenant_sort_columns(query))
            .offset(query.offset)
            .limit(query.limit)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all()), int(total or 0)


class TenantMembershipService:
    def __init__(self, session: AsyncSession, events: EventPublisher) -> None:
        self.session = session
        self.events = events

    async def list_members(
        self,
        *,
        tenant_id: str,
        query: TenantMemberListQuery,
        actor_id: str,
        authorization_decision: AuthorizationDecision | None = None,
    ) -> tuple[list[TenantMember], int]:
        _assert_member_authorized(
            authorization_decision=authorization_decision,
            tenant_id=tenant_id,
            actor_id=actor_id,
            actions={"read", "manage"},
            operation="Tenant member read",
        )
        filters = [TenantMember.tenant_id == tenant_id]
        if query.status is not None:
            filters.append(TenantMember.status == query.status.strip())
        if query.user_id is not None:
            filters.append(TenantMember.user_id == query.user_id.strip())
        if query.keyword is not None:
            keyword = f"%{query.keyword.strip().lower()}%"
            filters.append(func.lower(TenantMember.user_id).like(keyword))
        total = await self.session.scalar(
            select(func.count()).select_from(TenantMember).where(*filters)
        )
        statement = (
            select(TenantMember)
            .where(*filters)
            .order_by(*_member_sort_columns(query))
            .offset(query.offset)
            .limit(query.limit)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all()), int(total or 0)

    async def create_member(
        self,
        *,
        tenant_id: str,
        user_id: str,
        status: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None = None,
    ) -> TenantMember:
        _assert_member_authorized(
            authorization_decision=authorization_decision,
            tenant_id=tenant_id,
            actor_id=actor_id,
            actions={"manage"},
            operation="Tenant member mutation",
        )
        await self._assert_active_tenant(tenant_id)
        resolved_status = _clean_member_status(status)
        result = await self.session.execute(
            select(TenantMember)
            .where(TenantMember.tenant_id == tenant_id)
            .where(TenantMember.user_id == user_id)
        )
        member = result.scalars().first()
        previous_status: str | None
        if member is None:
            previous_status = None
            member = TenantMember(
                tenant_id=tenant_id,
                user_id=user_id,
                status=resolved_status,
            )
            self.session.add(member)
        else:
            previous_status = member.status
            member.status = resolved_status
        await self.session.flush()
        if member.status == "active" and previous_status != "active":
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

    async def update_member_status(
        self,
        *,
        tenant_id: str,
        member_id: str,
        status: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None = None,
    ) -> TenantMember:
        _assert_member_authorized(
            authorization_decision=authorization_decision,
            tenant_id=tenant_id,
            actor_id=actor_id,
            actions={"manage"},
            operation="Tenant member mutation",
        )
        member = await self.session.get(TenantMember, member_id)
        if member is None or member.tenant_id != tenant_id:
            raise AppError("NOT_FOUND", "Tenant member not found", status_code=404)
        previous_status = member.status
        member.status = _clean_member_status(status)
        await self.session.flush()
        if member.status == "active" and previous_status != "active":
            await publish_tenant_membership_event(
                self.events,
                TENANT_MEMBER_ACTIVATED_EVENT,
                tenant_id=tenant_id,
                user_id=member.user_id,
                member_id=member.id,
                status=member.status,
                actor_id=actor_id,
                request_id=request_id,
                extra={"change_type": "activated"},
            )
        return member

    async def _assert_active_tenant(self, tenant_id: str) -> None:
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            raise AppError("NOT_FOUND", f"Tenant {tenant_id!r} not found", status_code=404)
        if tenant.status != "active":
            raise AppError(
                "TENANT_STATE_FORBIDDEN",
                "Tenant must be active to manage members",
                status_code=403,
                details={"tenant_id": tenant_id, "status": tenant.status},
            )


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

    async def get_invitation(
        self,
        *,
        tenant_id: str,
        invitation_id: str,
    ) -> TenantInvitation:
        invitation = await self.session.get(TenantInvitation, invitation_id)
        if invitation is None or invitation.tenant_id != tenant_id:
            raise AppError("NOT_FOUND", "Tenant invitation not found", status_code=404)
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


def _assert_member_authorized(
    *,
    authorization_decision: AuthorizationDecision | None,
    tenant_id: str,
    actor_id: str,
    actions: set[str],
    operation: str,
) -> None:
    assert_authorization_decision(
        authorization_decision,
        tenant_id=tenant_id,
        actor_id=actor_id,
        resource="tenant_member",
        actions=actions,
        operation=operation,
    )


def _clean_member_status(status: str) -> str:
    resolved = status.strip().lower()
    if not resolved:
        raise AppError("VALIDATION_ERROR", "Tenant member status is required", status_code=400)
    return resolved


def _tenant_sort_columns(query: TenantListQuery):
    columns = {
        "created_at": Tenant.created_at,
        "code": Tenant.code,
        "name": Tenant.name,
        "status": Tenant.status,
    }
    return _sort_columns(query, columns)


def _member_sort_columns(query: TenantMemberListQuery):
    columns = {
        "created_at": TenantMember.created_at,
        "status": TenantMember.status,
        "user_id": TenantMember.user_id,
    }
    return _sort_columns(query, columns)


def _sort_columns(query: TenantListQuery | TenantMemberListQuery, columns: dict[str, object]):
    resolved = []
    for term in query.sort_terms():
        column = columns[term.field]
        resolved.append(column.desc() if term.direction == "desc" else column.asc())
    return tuple(resolved)


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
    "TenantMembershipService",
    "TenantQueryService",
]
