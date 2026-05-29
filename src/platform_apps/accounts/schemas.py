from __future__ import annotations

from datetime import datetime

from core.base import BaseSchema, CreateSchema, UpdateSchema


class UserRead(BaseSchema):
    id: str
    email: str
    display_name: str
    status: str
    auth_provider: str


class UserCreateRequest(CreateSchema):
    email: str
    display_name: str
    password: str


class UserProfileUpdateRequest(UpdateSchema):
    display_name: str


class PasswordResetRequest(UpdateSchema):
    current_password: str
    new_password: str


class PasswordResetRead(BaseSchema):
    password_updated: bool


class ExternalIdentityCreateRequest(CreateSchema):
    provider: str
    subject: str


class ExternalIdentityRead(BaseSchema):
    id: str
    user_id: str
    provider: str
    subject: str


class UserSessionRead(BaseSchema):
    id: str
    user_id: str
    tenant_id: str | None = None
    status: str
    auth_provider: str


class SessionRevokeRequest(CreateSchema):
    reason: str


class SessionRevokeRead(BaseSchema):
    revoked_sessions: int


class LoginRequest(CreateSchema):
    email: str
    password: str
    tenant_id: str | None = None


class LoginRead(BaseSchema):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    session: UserSessionRead


class TokenRefreshRead(LoginRead):
    pass


class ExternalAuthAuthorizeRead(BaseSchema):
    provider: str
    authorization_url: str
    state: str
    expires_at: datetime
    redirect_after: str | None = None


class ExternalAuthCallbackRequest(CreateSchema):
    code: str
    state: str


class UserSessionDetailRead(UserSessionRead):
    revoke_reason: str | None = None
    revoked_at: datetime | None = None
