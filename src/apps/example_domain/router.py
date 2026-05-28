from apps.example_domain.services import ExampleService
from core.base import create_router
from core.serialization import ok

router = create_router("/examples", tags=["examples"], public=True)


@router.get("/ping")
async def ping() -> dict[str, object]:
    return ok(ExampleService().ping().model_dump())
