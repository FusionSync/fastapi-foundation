from __future__ import annotations

from core.base import BaseSchema


class UserRead(BaseSchema):
    id: str
    email: str
    display_name: str
    status: str
    auth_provider: str


class UserSessionRead(BaseSchema):
    id: str
    user_id: str
    tenant_id: str | None = None
    status: str
    auth_provider: str
