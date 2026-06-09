from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import Model, TimestampMixin


class DatabaseLock(TimestampMixin, Model):
    __tablename__ = "core_locks"
    __table_args__ = (Index("ix_core_locks_expires_at", "expires_at"),)

    lock_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_token: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
