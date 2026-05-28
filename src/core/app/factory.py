from fastapi import FastAPI

from core.apps import AppRegistry
from core.config import Settings, get_settings, validate_startup_settings
from core.context import RequestContextMiddleware
from core.exceptions import register_exception_handlers
from core.observability import render_metrics_contract
from core.serialization import ok


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    validate_startup_settings(resolved_settings)

    app = FastAPI(title=resolved_settings.app.name, version=resolved_settings.app.version)
    app.state.settings = resolved_settings

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    _register_system_routes(app, resolved_settings)
    _register_app_modules(app, resolved_settings)
    return app


def _register_system_routes(app: FastAPI, settings: Settings) -> None:
    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, object]:
        return ok({"status": "alive"})

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> dict[str, object]:
        return ok({"status": "ready"})

    @app.get("/version", include_in_schema=False)
    async def version() -> dict[str, object]:
        return ok(
            {
                "name": settings.app.name,
                "version": settings.app.version,
                "env": settings.app.env,
            }
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> str:
        return render_metrics_contract()


def _register_app_modules(app: FastAPI, settings: Settings) -> None:
    registry = AppRegistry(settings.installed_apps).load()
    app.state.app_registry = registry
    for router in registry.routers:
        app.include_router(router, prefix=settings.api.prefix)
