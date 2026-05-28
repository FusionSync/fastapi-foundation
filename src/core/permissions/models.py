from __future__ import annotations

from uuid import uuid4

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import BaseModel, TimestampMixin


class RoleTemplate(TimestampMixin, BaseModel):
    __tablename__ = "role_templates"
    __table_args__ = (
        UniqueConstraint("scope", "name", "version", name="uq_role_templates_scope_name_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    permissions: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)


class RoleGrant(TimestampMixin, BaseModel):
    __tablename__ = "role_grants"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject_type",
            "subject_id",
            "role_template_id",
            name="uq_role_grants_tenant_subject_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role_template_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ProjectedPolicy(TimestampMixin, BaseModel):
    __tablename__ = "projected_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject",
            "resource",
            "action",
            "role_grant_id",
            name="uq_projected_policies_rule",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    resource: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False, default="allow")
    role_grant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
