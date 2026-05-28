from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Index, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import BaseModel, TimestampMixin

ProcessHeartbeatStatus = Literal["healthy", "unhealthy"]


class ProcessHeartbeat(TimestampMixin, BaseModel):
    __tablename__ = "process_heartbeats"
    __table_args__ = (
        UniqueConstraint("role", "instance_id", name="uq_process_heartbeats_role_instance_id"),
        Index("ix_process_heartbeats_role_last_seen_at", "role", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="healthy")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True, slots=True)
class ProcessHeartbeatSnapshot:
    role: str
    instance_id: str
    status: str
    last_seen_at: datetime
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_model(cls, heartbeat: ProcessHeartbeat) -> ProcessHeartbeatSnapshot:
        return cls(
            role=heartbeat.role,
            instance_id=heartbeat.instance_id,
            status=heartbeat.status,
            last_seen_at=_ensure_aware(heartbeat.last_seen_at),
            details=dict(heartbeat.details),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "instance_id": self.instance_id,
            "status": self.status,
            "last_seen_at": _ensure_aware(self.last_seen_at).isoformat(),
            "details": self.details,
        }


class ProcessHeartbeatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        role: str,
        instance_id: str,
        status: ProcessHeartbeatStatus = "healthy",
        details: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ProcessHeartbeatSnapshot:
        seen_at = _ensure_aware(now or datetime.now(UTC))
        result = await self.session.execute(
            select(ProcessHeartbeat).where(
                ProcessHeartbeat.role == role,
                ProcessHeartbeat.instance_id == instance_id,
            )
        )
        heartbeat = result.scalar_one_or_none()
        if heartbeat is None:
            heartbeat = ProcessHeartbeat(
                role=role,
                instance_id=instance_id,
                status=status,
                details=dict(details or {}),
                last_seen_at=seen_at,
            )
            self.session.add(heartbeat)
        else:
            heartbeat.status = status
            heartbeat.details = dict(details or {})
            heartbeat.last_seen_at = seen_at

        await self.session.flush()
        return ProcessHeartbeatSnapshot.from_model(heartbeat)

    async def latest(self, role: str) -> ProcessHeartbeatSnapshot | None:
        result = await self.session.execute(
            select(ProcessHeartbeat)
            .where(ProcessHeartbeat.role == role)
            .order_by(ProcessHeartbeat.last_seen_at.desc())
            .limit(1)
        )
        heartbeat = result.scalar_one_or_none()
        if heartbeat is None:
            return None
        return ProcessHeartbeatSnapshot.from_model(heartbeat)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
