"""Access app product-side IAM models."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import Model, TimestampMixin


class FrontendAccessMapping(TimestampMixin, Model):
    __tablename__ = "frontend_access_mappings"
    __table_args__ = (
        UniqueConstraint("client_id", "access_key", name="uq_frontend_access_client_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    access_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    owner_module: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    expression_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class FrontendAccessMappingRevision(TimestampMixin, Model):
    __tablename__ = "frontend_access_mapping_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    mapping_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    access_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    old_expression_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    new_expression_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    old_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
