from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.context.context import get_current_context
from core.messages import resolve_message
from core.serialization.encoders import to_jsonable


class Pagination(BaseModel):
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    has_next: bool


class Envelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    message: str
    data: Any | None = None
    items: list[Any] | None = Field(default=None, alias="list")
    pagination: Pagination | None = None
    details: dict[str, Any] | None = None
    request_id: str


def _request_id(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    context = get_current_context()
    return context.request_id if context else "req_unknown"


def ok(
    data: Any | None = None,
    *,
    message: str = "success",
    request_id: str | None = None,
) -> dict[str, Any]:
    return Envelope(
        code="OK",
        message=message,
        data=to_jsonable(data),
        items=None,
        pagination=None,
        details=None,
        request_id=_request_id(request_id),
    ).model_dump(mode="json", by_alias=True)


def ok_list(
    items: list[Any],
    pagination: Pagination | dict[str, Any],
    *,
    message: str = "success",
    request_id: str | None = None,
) -> dict[str, Any]:
    pagination_model = (
        pagination if isinstance(pagination, Pagination) else Pagination(**pagination)
    )
    return Envelope(
        code="OK",
        message=message,
        data=None,
        items=to_jsonable(items),
        pagination=pagination_model,
        details=None,
        request_id=_request_id(request_id),
    ).model_dump(mode="json", by_alias=True)


def fail(
    code: str,
    *,
    message: str | None = None,
    details: dict[str, Any] | None = None,
    locale: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return Envelope(
        code=code,
        message=message or resolve_message(code, locale=locale),
        data=None,
        items=None,
        pagination=None,
        details=to_jsonable(details),
        request_id=_request_id(request_id),
    ).model_dump(mode="json", by_alias=True)
