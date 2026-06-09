from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import Model, TimestampMixin

IdempotencyStatus = Literal["processing", "succeeded", "failed", "expired"]


class IdempotencyRecord(TimestampMixin, Model):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "route",
            "idempotency_key",
            name="uq_idempotency_records_scope_key",
        ),
        Index("ix_idempotency_records_status_expires_at", "status", "expires_at"),
        Index("ix_idempotency_records_locked_until", "locked_until"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    route: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    response_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_body: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outbox_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
