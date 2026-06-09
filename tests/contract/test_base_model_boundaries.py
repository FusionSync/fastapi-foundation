from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy.orm import DeclarativeBase

from core.base import CreateSchema, ListQuerySchema, ReadSchema, Schema, UpdateSchema
from core.base.models import Model, TenantScopedModel


class ContractPayload(Schema):
    name: str


def test_pydantic_contract_root_is_schema() -> None:
    assert issubclass(Schema, PydanticBaseModel)
    assert issubclass(CreateSchema, Schema)
    assert issubclass(UpdateSchema, Schema)
    assert issubclass(ReadSchema, Schema)
    assert issubclass(ListQuerySchema, Schema)

    payload = ContractPayload(name="demo")

    assert payload.model_dump() == {"name": "demo"}


def test_orm_root_base_is_named_model() -> None:
    assert issubclass(Model, DeclarativeBase)
    assert issubclass(TenantScopedModel, Model)
