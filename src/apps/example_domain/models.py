from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import IdMixin, TenantScopedModel, TimestampMixin


class ExampleRecord(IdMixin, TimestampMixin, TenantScopedModel):
    __tablename__ = "example_domain_records"

    title: Mapped[str] = mapped_column(String(128), nullable=False)
