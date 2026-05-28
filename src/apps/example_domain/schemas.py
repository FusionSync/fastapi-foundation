from typing import ClassVar

from core.base import BaseSchema, CreateSchema, ListQuerySchema, ReadSchema, UpdateSchema


class ExampleCreate(CreateSchema):
    title: str


class ExampleUpdate(UpdateSchema):
    title: str | None = None


class ExampleRead(ReadSchema):
    tenant_id: str
    title: str


class ExampleListQuery(ListQuerySchema):
    sortable_fields: ClassVar[frozenset[str] | None] = frozenset({"created_at", "title"})
    filterable_fields: ClassVar[frozenset[str] | None] = frozenset({"keyword", "title"})
    default_sort: ClassVar[tuple[str, ...]] = ("-created_at",)

    title: str | None = None


class ExamplePing(BaseSchema):
    app: str
    status: str
