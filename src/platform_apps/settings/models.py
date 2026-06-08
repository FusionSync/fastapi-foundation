from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import BaseModel, TimestampMixin


class SettingValue(TimestampMixin, BaseModel):
    __tablename__ = "setting_values"
    __table_args__ = (
        UniqueConstraint(
            "module",
            "key",
            "scope",
            "scope_id",
            name="uq_setting_values_module_key_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    module: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    value_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SettingRevision(TimestampMixin, BaseModel):
    __tablename__ = "setting_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    setting_value_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("setting_values.id"),
        nullable=False,
        index=True,
    )
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    new_value_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    old_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
