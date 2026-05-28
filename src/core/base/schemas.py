import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from core.serialization.responses import Pagination

_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LIST_QUERY_CONTROL_FIELDS = frozenset({"page", "page_size", "sort"})


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ReadSchema(BaseSchema):
    id: UUID | str
    created_at: datetime
    updated_at: datetime


class CreateSchema(BaseSchema):
    pass


class UpdateSchema(BaseSchema):
    pass


class SortTerm(BaseSchema):
    field: str
    direction: Literal["asc", "desc"] = "asc"


class ListQuerySchema(BaseSchema):
    sortable_fields: ClassVar[frozenset[str] | None] = None
    default_sort: ClassVar[tuple[str, ...]] = ()
    filterable_fields: ClassVar[frozenset[str] | None] = None

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)
    sort: str | None = None
    keyword: str | None = None

    @field_validator("keyword", mode="before")
    @classmethod
    def _empty_keyword_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_query_contract(self) -> "ListQuerySchema":
        self.sort_terms()
        self.filter_values()
        return self

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size

    def to_pagination(self, *, total: int) -> "Pagination":
        from core.serialization.responses import Pagination

        return Pagination(
            total=total,
            page=self.page,
            page_size=self.page_size,
            has_next=self.offset + self.page_size < total,
        )

    def sort_terms(self) -> tuple[SortTerm, ...]:
        sort_value = self.sort
        if sort_value is None and self.default_sort:
            sort_value = ",".join(self.default_sort)
        if sort_value is None or not sort_value.strip():
            return ()

        terms: list[SortTerm] = []
        seen_fields: set[str] = set()
        for raw_token in sort_value.split(","):
            token = raw_token.strip()
            if not token:
                raise ValueError("sort token cannot be empty")

            direction: Literal["asc", "desc"] = "asc"
            field_name = token
            if token[0] in {"+", "-"}:
                direction = "desc" if token[0] == "-" else "asc"
                field_name = token[1:]

            if not _FIELD_NAME_RE.fullmatch(field_name):
                raise ValueError(f"invalid sort field: {field_name!r}")
            if field_name in seen_fields:
                raise ValueError(f"duplicate sort field: {field_name!r}")
            if self.sortable_fields is not None and field_name not in self.sortable_fields:
                raise ValueError(f"sort field is not allowed: {field_name!r}")

            seen_fields.add(field_name)
            terms.append(SortTerm(field=field_name, direction=direction))
        return tuple(terms)

    def filter_values(self) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        allowed_fields = self.filterable_fields
        values = self.model_dump(exclude_none=True, exclude=_LIST_QUERY_CONTROL_FIELDS)
        for field_name, value in values.items():
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            if allowed_fields is not None and field_name not in allowed_fields:
                raise ValueError(f"filter field is not allowed: {field_name!r}")
            filters[field_name] = value
        return filters
