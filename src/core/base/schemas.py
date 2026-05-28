from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class ListQuerySchema(BaseSchema):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)
    sort: str | None = None
    keyword: str | None = None
