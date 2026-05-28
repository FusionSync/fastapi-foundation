from typing import Any, ClassVar

import pytest
from pydantic import ValidationError
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from core.base import ListQuerySchema
from core.base.models import IdMixin, SoftDeleteMixin, TenantScopedModel, TimestampMixin
from core.base.repositories import TenantScopedRepository
from core.context import RequestContext, reset_current_context, set_current_context


class TenantThing(IdMixin, SoftDeleteMixin, TimestampMixin, TenantScopedModel):
    __tablename__ = "test_list_query_tenant_things"

    name: Mapped[str] = mapped_column(String(64), nullable=False)


class TenantThingListQuery(ListQuerySchema):
    sortable_fields: ClassVar[frozenset[str] | None] = frozenset({"created_at", "name"})
    filterable_fields: ClassVar[frozenset[str] | None] = frozenset({"keyword", "name"})

    name: str | None = None


class TenantThingRepository(TenantScopedRepository[TenantThing]):
    model = TenantThing


def test_list_query_schema_parses_pagination_sort_and_filters() -> None:
    query = TenantThingListQuery(
        page=3,
        page_size=25,
        sort="-created_at,name",
        keyword="demo",
        name="alpha",
    )

    assert query.offset == 50
    assert query.limit == 25
    assert [term.model_dump() for term in query.sort_terms()] == [
        {"field": "created_at", "direction": "desc"},
        {"field": "name", "direction": "asc"},
    ]
    assert query.filter_values() == {"keyword": "demo", "name": "alpha"}
    assert query.to_pagination(total=76).model_dump() == {
        "total": 76,
        "page": 3,
        "page_size": 25,
        "has_next": True,
    }


@pytest.mark.parametrize("sort", ["-unknown", "name,,created_at", "name;drop", "-"])
def test_list_query_schema_rejects_unsafe_or_unknown_sort_fields(sort: str) -> None:
    with pytest.raises(ValidationError):
        TenantThingListQuery(sort=sort)


def test_tenant_repository_applies_list_query_sort_filter_and_pagination() -> None:
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        repo = TenantThingRepository(_FakeSession())  # type: ignore[arg-type]
        query = TenantThingListQuery(page=2, page_size=10, sort="-created_at,name", name="demo")

        statement = repo.apply_list_query(
            repo.query(),
            query,
            sort_columns={
                "created_at": TenantThing.created_at,
                "name": TenantThing.name,
            },
            filter_columns={"name": TenantThing.name},
        )
        sql = str(statement.compile(compile_kwargs={"literal_binds": True}))

        assert "test_list_query_tenant_things.tenant_id = 'tenant-a'" in sql
        assert "test_list_query_tenant_things.name = 'demo'" in sql
        assert (
            "ORDER BY test_list_query_tenant_things.created_at DESC, "
            "test_list_query_tenant_things.name ASC"
        ) in sql
        assert "LIMIT 10 OFFSET 10" in sql
    finally:
        reset_current_context(token)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, record: Any) -> None:
        self.added.append(record)
