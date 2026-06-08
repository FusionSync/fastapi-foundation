from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import SessionPrincipal
from platform_apps.accounts.models import User, UserSession


class AccountsAuthSessionStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_principal(self, session_id: str) -> SessionPrincipal | None:
        user_session = await self.session.get(UserSession, session_id)
        if user_session is None:
            return None
        user = await self.session.get(User, user_session.user_id)
        if user is None:
            return None
        return SessionPrincipal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            auth_provider=user_session.auth_provider,
            session_id=user_session.id,
            session_status=user_session.status,
            user_status=user.status,
            session_token_version=user_session.token_version,
            user_token_version=user.token_version,
            tenant_id=user_session.tenant_id,
        )
