from apps.example_domain.schemas import ExamplePing
from apps.example_domain.services import ExampleService
from core.base import create_router
from core.serialization import Envelope, ok

router = create_router("/examples", tags=["examples"], public=True)


@router.get("/ping", response_model=Envelope[ExamplePing])
async def ping() -> dict[str, object]:
    return ok(ExampleService().ping().model_dump())
