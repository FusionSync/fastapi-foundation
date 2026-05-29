from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import BaseModel, TimestampMixin

TenantLifecycleStepStatus = Literal["pending", "succeeded", "failed"]


class Tenant(TimestampMixin, BaseModel):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("code", name="uq_tenants_code"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="provisioning")
    deployment_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="local")


class TenantMember(TimestampMixin, BaseModel):
    __tablename__ = "tenant_members"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_members_tenant_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class TenantInvitation(TimestampMixin, BaseModel):
    __tablename__ = "tenant_invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_tenant_invitations_token_hash"),
        Index(
            "ix_tenant_invitations_tenant_email_status",
            "tenant_id",
            "email",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role_template_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    invited_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role_grant_authorized_by_user_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    role_grant_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TenantLifecycleStepRecord(TimestampMixin, BaseModel):
    __tablename__ = "tenant_lifecycle_step_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "workflow",
            "step",
            name="uq_tenant_lifecycle_step_tenant_workflow_step",
        ),
        Index(
            "ix_tenant_lifecycle_step_workflow_status",
            "workflow",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workflow: Mapped[str] = mapped_column(String(64), nullable=False)
    step: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    forward_fix_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
