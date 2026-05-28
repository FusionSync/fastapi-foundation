from typing import Any, Generic, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.base.models import BaseModel
from core.context import get_current_context
from core.exceptions import AppError

ModelT = TypeVar("ModelT", bound=BaseModel)


class TenantScopedQuery(Generic[ModelT]):
    def __init__(self, model: type[ModelT], tenant_id: str) -> None:
        self.model = model
        self.tenant_id = tenant_id

    def select(self) -> Select[tuple[ModelT]]:
        return self.apply(select(self.model))

    def apply(self, statement: Select[tuple[ModelT]]) -> Select[tuple[ModelT]]:
        statement = statement.where(self.model.tenant_id == self.tenant_id)  # type: ignore[attr-defined]
        if hasattr(self.model, "deleted_at"):
            statement = statement.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return statement

    def by_id(self, record_id: object) -> Select[tuple[ModelT]]:
        return self.select().where(self.model.id == record_id)  # type: ignore[attr-defined]


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def query(self) -> Select[tuple[ModelT]]:
        return select(self.model)

    async def get(self, record_id: object) -> ModelT | None:
        result = await self.session.execute(self.query().where(self.model.id == record_id))  # type: ignore[attr-defined]
        return result.scalars().first()


class TenantScopedRepository(BaseRepository[ModelT]):
    def current_tenant_id(self) -> str:
        context = get_current_context()
        if not context or not context.tenant_id:
            raise AppError(
                "TENANT_ACCESS_DENIED",
                "TenantScopedRepository requires tenant context",
                status_code=403,
            )
        return context.tenant_id

    def scoped_query(self) -> TenantScopedQuery[ModelT]:
        return TenantScopedQuery(self.model, self.current_tenant_id())

    def query(self) -> Select[tuple[ModelT]]:
        return self.scoped_query().select()

    async def get(self, record_id: object) -> ModelT | None:
        result = await self.session.execute(self.scoped_query().by_id(record_id))
        return result.scalars().first()

    async def list(self) -> list[ModelT]:
        result = await self.session.execute(self.query())
        return list(result.scalars().all())

    async def create(self, values: dict[str, Any] | ModelT) -> ModelT:
        tenant_id = self.current_tenant_id()
        if isinstance(values, dict):
            provided_tenant_id = values.get("tenant_id")
            if provided_tenant_id is not None and provided_tenant_id != tenant_id:
                raise AppError(
                    "TENANT_CONTEXT_CONFLICT",
                    "Cannot create tenant-scoped data for another tenant",
                    status_code=403,
                )
            values = {**values, "tenant_id": tenant_id}
            record = self.model(**values)
        else:
            provided_tenant_id = getattr(values, "tenant_id", None)
            if provided_tenant_id is not None and provided_tenant_id != tenant_id:
                raise AppError(
                    "TENANT_CONTEXT_CONFLICT",
                    "Cannot create tenant-scoped data for another tenant",
                    status_code=403,
                )
            values.tenant_id = tenant_id  # type: ignore[attr-defined]
            record = values
        self.session.add(record)
        return record

    async def update(self, record_id: object, values: dict[str, Any]) -> ModelT | None:
        if "tenant_id" in values and values["tenant_id"] != self.current_tenant_id():
            raise AppError(
                "TENANT_CONTEXT_CONFLICT",
                "Cannot move tenant-scoped data across tenants",
                status_code=403,
            )
        record = await self.get(record_id)
        if record is None:
            return None
        for key, value in values.items():
            if key != "tenant_id":
                setattr(record, key, value)
        return record


class CrossTenantRepository(BaseRepository[ModelT]):
    def __init__(
        self,
        session: AsyncSession,
        *,
        reason: str,
        platform_permission_granted: bool,
    ) -> None:
        if not reason.strip():
            raise AppError(
                "PERMISSION_DENIED",
                "Cross-tenant access requires an audit reason",
                status_code=403,
            )
        if not platform_permission_granted:
            raise AppError(
                "PERMISSION_DENIED",
                "Cross-tenant access requires platform permission",
                status_code=403,
            )
        super().__init__(session)
        self.reason = reason
