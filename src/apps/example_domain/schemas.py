from core.base import BaseSchema, CreateSchema, ReadSchema, UpdateSchema


class ExampleCreate(CreateSchema):
    title: str


class ExampleUpdate(UpdateSchema):
    title: str | None = None


class ExampleRead(ReadSchema):
    tenant_id: str
    title: str


class ExamplePing(BaseSchema):
    app: str
    status: str
