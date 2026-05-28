from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditRecorder
from core.auth import invalid_auth_token
from core.exceptions import AppError
from core.permissions import (
    PLATFORM_TENANT_ID,
    AuthorizationDecision,
    assert_authorization_decision,
)
from core.security import PasswordHasher
from core.tenancy import Tenant, TenantMember, assert_tenant_operation_allowed
from platform_apps.accounts.models import ExternalIdentity, User, UserCredential, UserSession


class AccountsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        audit: AuditRecorder | None = None,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        self.session = session
        self.audit = audit
        self.password_hasher = password_hasher or PasswordHasher()

    async def create_user(
        self,
        *,
        email: str,
        display_name: str,
        auth_provider: str,
        external_id: str | None = None,
    ) -> User:
        normalized_email = email.strip().lower()
        self._validate_user_input(
            email=normalized_email,
            display_name=display_name,
            auth_provider=auth_provider,
        )
        user = User(
            email=normalized_email,
            display_name=display_name,
            status="active",
            auth_provider=auth_provider,
            external_id=external_id,
            token_version=1,
        )
        self.session.add(user)
        await self.session.flush()
        if external_id:
            self.session.add(
                ExternalIdentity(
                    user_id=user.id,
                    provider=auth_provider,
                    subject=external_id,
                )
            )
            await self.session.flush()
        return user

    async def create_local_user(
        self,
        *,
        email: str,
        display_name: str,
        password: str,
    ) -> User:
        normalized_email = email.strip().lower()
        user = await self.create_user(
            email=normalized_email,
            display_name=display_name,
            auth_provider="local",
            external_id=normalized_email,
        )
        self.session.add(
            UserCredential(
                user_id=user.id,
                password_hash=self.password_hasher.hash_password(password),
            )
        )
        await self.session.flush()
        return user

    async def verify_local_password(self, *, email: str, password: str) -> User:
        normalized_email = email.strip().lower()
        result = await self.session.execute(
            select(User)
            .where(User.email == normalized_email)
            .where(User.auth_provider == "local")
        )
        user = result.scalars().first()
        if user is None:
            invalid_auth_token("invalid_credentials")
        if user.status != "active":
            invalid_auth_token("user_not_active")
        credential = await self.session.get(UserCredential, user.id)
        if credential is None:
            invalid_auth_token("missing_credentials")
        if not self.password_hasher.verify_password(password, credential.password_hash):
            invalid_auth_token("invalid_credentials")
        return user

    async def create_session(
        self,
        *,
        user_id: str,
        tenant_id: str | None,
        auth_provider: str,
        request_id: str | None = None,
    ) -> UserSession:
        user = await self._get_user(user_id)
        if user.status != "active":
            raise AppError(
                "USER_DISABLED",
                "disabled users cannot create sessions",
                status_code=403,
            )
        if tenant_id is not None:
            await self._assert_tenant_login_allowed(user_id=user.id, tenant_id=tenant_id)
        session = UserSession(
            user_id=user.id,
            tenant_id=tenant_id,
            auth_provider=auth_provider,
            status="active",
            token_version=user.token_version,
        )
        self.session.add(session)
        await self.session.flush()
        if self.audit is not None:
            await self.audit.record(
                action="session.created",
                resource_type="user_session",
                resource_id=session.id,
                result="success",
                tenant_id=tenant_id,
                actor_id=user.id,
                auth_provider=auth_provider,
                session_id=session.id,
                request_id=request_id,
                payload={"token_version": session.token_version},
            )
        return session

    async def _assert_tenant_login_allowed(self, *, user_id: str, tenant_id: str) -> None:
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            raise AppError(
                "TENANT_ACCESS_DENIED",
                "tenant membership is required to create a session",
                status_code=403,
                details={"tenant_id": tenant_id},
            )
        result = await self.session.execute(
            select(TenantMember)
            .where(TenantMember.tenant_id == tenant_id)
            .where(TenantMember.user_id == user_id)
            .where(TenantMember.status == "active")
        )
        if result.scalars().first() is None:
            raise AppError(
                "TENANT_ACCESS_DENIED",
                "active tenant membership is required to create a session",
                status_code=403,
                details={"tenant_id": tenant_id, "user_id": user_id},
            )
        assert_tenant_operation_allowed(
            tenant_id=tenant_id,
            status=tenant.status,  # type: ignore[arg-type]
            operation="login",
        )

    async def disable_user(
        self,
        user_id: str,
        *,
        reason: str,
        actor_id: str | None = None,
        request_id: str | None = None,
        authorization_decision: AuthorizationDecision | None = None,
    ) -> User:
        _assert_accounts_mutation_authorized(
            authorization_decision=authorization_decision,
            actor_id=actor_id,
            resource="user",
            mutation="disable",
            operation="User disable",
        )
        user = await self._get_user(user_id)
        if user.status != "disabled":
            user.status = "disabled"
            user.token_version += 1
        revoked_sessions = await self._revoke_user_sessions(user_id, reason)
        if self.audit is not None:
            await self.audit.record(
                action="user.disabled",
                resource_type="user",
                resource_id=user.id,
                result="success",
                actor_id=actor_id,
                reason=reason,
                request_id=request_id,
                payload={
                    "revoked_sessions": revoked_sessions,
                    "token_version": user.token_version,
                },
            )
        await self.session.flush()
        return user

    async def revoke_user_sessions(
        self,
        user_id: str,
        reason: str,
        *,
        actor_id: str | None = None,
        request_id: str | None = None,
        authorization_decision: AuthorizationDecision | None = None,
    ) -> int:
        _assert_accounts_mutation_authorized(
            authorization_decision=authorization_decision,
            actor_id=actor_id,
            resource="session",
            mutation="revoke",
            operation="Session revoke",
        )
        revoked_sessions = await self._revoke_user_sessions(user_id, reason)
        if self.audit is not None:
            await self.audit.record(
                action="session.revoked",
                resource_type="user_session",
                resource_id=user_id,
                result="success",
                actor_id=actor_id,
                reason=reason,
                request_id=request_id,
                payload={"scope": "user", "revoked_sessions": revoked_sessions},
            )
        return revoked_sessions

    async def revoke_tenant_sessions(
        self,
        tenant_id: str,
        reason: str,
        *,
        actor_id: str | None = None,
        request_id: str | None = None,
        authorization_decision: AuthorizationDecision | None = None,
    ) -> int:
        _assert_accounts_mutation_authorized(
            authorization_decision=authorization_decision,
            actor_id=actor_id,
            resource="session",
            mutation="revoke",
            operation="Session revoke",
        )
        revoked_sessions = await self._revoke_tenant_sessions(tenant_id, reason)
        if self.audit is not None:
            await self.audit.record(
                action="session.revoked",
                resource_type="tenant_session",
                resource_id=tenant_id,
                result="success",
                actor_id=actor_id,
                reason=reason,
                request_id=request_id,
                payload={"scope": "tenant", "revoked_sessions": revoked_sessions},
            )
        return revoked_sessions

    async def revoke_tenant_sessions_for_lifecycle(self, tenant_id: str, reason: str) -> int:
        return await self._revoke_tenant_sessions(tenant_id, reason)

    async def _revoke_user_sessions(self, user_id: str, reason: str) -> int:
        sessions = await self._active_sessions(user_id=user_id)
        self._revoke_sessions(sessions, reason)
        await self.session.flush()
        return len(sessions)

    async def _revoke_tenant_sessions(self, tenant_id: str, reason: str) -> int:
        sessions = await self._active_sessions(tenant_id=tenant_id)
        self._revoke_sessions(sessions, reason)
        await self.session.flush()
        return len(sessions)

    async def _get_user(self, user_id: str) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise AppError("NOT_FOUND", f"User {user_id!r} not found", status_code=404)
        return user

    async def _active_sessions(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[UserSession]:
        statement = select(UserSession).where(UserSession.status == "active")
        if user_id is not None:
            statement = statement.where(UserSession.user_id == user_id)
        if tenant_id is not None:
            statement = statement.where(UserSession.tenant_id == tenant_id)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    def _revoke_sessions(self, sessions: list[UserSession], reason: str) -> None:
        revoked_at = datetime.now(UTC)
        for session in sessions:
            session.status = "revoked"
            session.revoke_reason = reason
            session.revoked_at = revoked_at

    def _validate_user_input(
        self,
        *,
        email: str,
        display_name: str,
        auth_provider: str,
    ) -> None:
        if not email or "@" not in email:
            raise AppError("VALIDATION_ERROR", "valid email is required", status_code=400)
        if not display_name.strip():
            raise AppError("VALIDATION_ERROR", "display_name is required", status_code=400)
        if not auth_provider.strip():
            raise AppError("VALIDATION_ERROR", "auth_provider is required", status_code=400)


def _assert_accounts_mutation_authorized(
    *,
    authorization_decision: AuthorizationDecision | None,
    actor_id: str | None,
    resource: str,
    mutation: str,
    operation: str,
) -> None:
    assert_authorization_decision(
        authorization_decision,
        tenant_id=PLATFORM_TENANT_ID,
        actor_id=actor_id or "",
        resource=resource,
        actions={"manage", mutation},
        operation=operation,
        allow_platform=False,
    )
