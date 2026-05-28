from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from core.apps import AppRegistry
from core.config import Settings, get_settings, validate_startup_settings
from core.context import RequestContextMiddleware
from core.exceptions import register_exception_handlers
from core.observability import HttpMetricsMiddleware, MetricsRegistry, render_metrics_contract
from core.security import (
    RequestBodySizeLimitMiddleware,
    SecretProvider,
    SecurityHeadersMiddleware,
    TrustedHostGuardMiddleware,
    resolve_settings_secrets,
)
from core.serialization import ok


def create_app(
    settings: Settings | None = None,
    *,
    secret_provider: SecretProvider | None = None,
) -> FastAPI:
    resolved_settings = resolve_settings_secrets(settings or get_settings(), secret_provider)
    validate_startup_settings(resolved_settings)

    app = FastAPI(title=resolved_settings.app.name, version=resolved_settings.app.version)
    app.state.settings = resolved_settings
    app.state.metrics_registry = MetricsRegistry()

    _register_security_middleware(app, resolved_settings)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(HttpMetricsMiddleware)
    register_exception_handlers(app)
    _register_system_routes(app, resolved_settings)
    _register_app_modules(app, resolved_settings)
    return app


def _register_security_middleware(app: FastAPI, settings: Settings) -> None:
    if settings.security.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.security.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    if settings.security.trusted_hosts:
        app.add_middleware(
            TrustedHostGuardMiddleware,
            allowed_hosts=settings.security.trusted_hosts,
        )
    if settings.security.max_request_body_bytes is not None:
        app.add_middleware(
            RequestBodySizeLimitMiddleware,
            max_body_bytes=settings.security.max_request_body_bytes,
        )
    app.add_middleware(SecurityHeadersMiddleware)


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
    async def metrics() -> Response:
        return Response(
            render_metrics_contract(app.state.metrics_registry),
            media_type="text/plain; version=0.0.4",
        )


def _register_app_modules(app: FastAPI, settings: Settings) -> None:
    registry = AppRegistry(settings.installed_apps).load()
    app.state.app_registry = registry
    for router in registry.routers:
        app.include_router(router, prefix=settings.api.prefix)
