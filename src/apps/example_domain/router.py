from typing import Annotated

from fastapi import Depends

from apps.example_domain.schemas import ExampleListQuery, ExamplePing, ExampleRead
from apps.example_domain.services import ExampleService
from core.base import create_router
from core.serialization import Envelope, ListEnvelope, ok, ok_list

router = create_router("/examples", tags=["examples"], public=True)


@router.get("", response_model=ListEnvelope[ExampleRead])
async def list_examples(query: Annotated[ExampleListQuery, Depends()]) -> dict[str, object]:
    items, total = ExampleService().list_examples(query)
    return ok_list(items, query.to_pagination(total=total))


@router.get("/ping", response_model=Envelope[ExamplePing])
async def ping() -> dict[str, object]:
    return ok(ExampleService().ping().model_dump())
