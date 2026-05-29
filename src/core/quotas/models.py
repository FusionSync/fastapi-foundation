from __future__ import annotations

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import BaseModel, TimestampMixin


class QuotaUsage(TimestampMixin, BaseModel):
    __tablename__ = "quota_usage"
    __table_args__ = (Index("ix_quota_usage_used", "used"),)

    usage_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
