import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from swagger_ui import starlette_api_doc

from core.admin import AdminRegistry, build_admin_router
from core.app.diagnostics import build_startup_diagnostics, merge_provider_readiness
from core.app.lifecycle import run_lifecycle_hooks
from core.apps import AppRegistry, resolve_runtime_capabilities
from core.apps.conformance import AppCheckResult, check_apps
from core.auth.jwt_provider import LocalJwtConfig, LocalJwtProvider
from core.auth.request_security import DatabaseRequestSecurityPipeline, SessionStoreFactory
from core.config import Settings, get_settings, validate_startup_settings
from core.context import RequestContextMiddleware
from core.db import DatabaseRuntime, create_database_runtime
from core.events import EventRegistry
from core.exceptions import register_exception_handlers
from core.migrations import MigrationRegistry
from core.observability import (
    HttpMetricsMiddleware,
    HttpRequestLoggingMiddleware,
    MetricsRegistry,
    render_metrics_contract,
)
from core.operations import DatabaseReadinessProbe, check_app_readiness
from core.permissions import PermissionRegistry
from core.rate_limit import RateLimitMiddleware
from core.scheduler import ScheduleRegistry
from core.security import (
    RequestBodySizeLimitMiddleware,
    SecretProvider,
    SecurityHeadersMiddleware,
    TrustedHostGuardMiddleware,
    resolve_settings_secrets,
)
from core.serialization import ok
from core.tasks import TaskRegistry
from core.tenancy import tenant_lifecycle_policy_from_settings

_IMPORTED_APP_MODEL_MODULES: set[str] = set()


def create_app(
    settings: Settings | None = None,
    *,
    secret_provider: SecretProvider | None = None,
    request_security_pipeline: DatabaseRequestSecurityPipeline | None = None,
) -> FastAPI:
    resolved_settings = resolve_settings_secrets(settings or get_settings(), secret_provider)
    validate_startup_settings(resolved_settings)

    database_runtime = create_database_runtime(resolved_settings)
    app = FastAPI(
        title=resolved_settings.app.name,
        version=resolved_settings.app.version,
        docs_url=None,
        redoc_url=None,
        lifespan=_app_lifespan(database_runtime),
    )
    app.state.settings = resolved_settings
    app.state.database_engine = database_runtime.engine
    app.state.session_factory = database_runtime.session_factory
    app.state.metrics_registry = MetricsRegistry()
    app.state.readiness_database_probe = DatabaseReadinessProbe(resolved_settings.database.url)

    _register_local_docs(app, resolved_settings)
    _register_security_middleware(app, resolved_settings)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(HttpRequestLoggingMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(HttpMetricsMiddleware)
    register_exception_handlers(app)
    _register_system_routes(app, resolved_settings)
    registry = _register_app_modules(app, resolved_settings)
    _configure_request_security(
        app,
        settings=resolved_settings,
        registry=registry,
        request_security_pipeline=request_security_pipeline,
    )
    app.state.startup_diagnostics = build_startup_diagnostics(app)
    return app


def _app_lifespan(database_runtime: DatabaseRuntime):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await run_lifecycle_hooks(app, phase="startup")
            try:
                yield
            finally:
                await run_lifecycle_hooks(app, phase="shutdown")
        finally:
            await database_runtime.dispose()

    return lifespan


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


def _register_local_docs(app: FastAPI, settings: Settings) -> None:
    starlette_api_doc(
        app,
        url_prefix="/docs",
        base_url="/docs",
        config_rel_url=app.openapi_url or "/openapi.json",
        title=f"{settings.app.name} - API Docs",
    )

    @app.get("/docs/openapi.json", include_in_schema=False)
    async def swagger_ui_openapi_schema():
        return app.openapi()


def _register_system_routes(app: FastAPI, settings: Settings) -> None:
    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, object]:
        return ok({"status": "alive"})

    @app.get("/readyz", include_in_schema=False)
    async def readyz(response: Response) -> dict[str, object]:
        database_probe = getattr(app.state, "readiness_database_probe", None)
        dependency_results = {}
        if database_probe is not None:
            dependency_results["database"] = await database_probe.check()
        readiness = check_app_readiness(
            settings=settings,
            app_registry=getattr(app.state, "app_registry", None),
            metrics_registry=getattr(app.state, "metrics_registry", None),
            dependency_results=dependency_results,
            lifecycle_diagnostics=getattr(app.state, "lifecycle_diagnostics", None),
            startup_diagnostics=merge_provider_readiness(
                getattr(app.state, "startup_diagnostics", None),
                dependency_results,
            ),
        )
        if not readiness.ok:
            response.status_code = 503
        return ok(readiness.to_dict())

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


def _register_app_modules(app: FastAPI, settings: Settings) -> AppRegistry:
    _validate_installed_apps(settings.installed_apps)
    registry = AppRegistry(
        settings.installed_apps,
        runtime_capabilities=resolve_runtime_capabilities(settings),
    ).load()
    imported_models = _import_app_models(registry)
    app.state.app_registry = registry
    app.state.app_model_modules = imported_models
    _assemble_app_runtime_registries(app, registry)
    for router in registry.routers:
        app.include_router(router, prefix=settings.api.prefix)
    return registry


def _configure_request_security(
    app: FastAPI,
    *,
    settings: Settings,
    registry: AppRegistry,
    request_security_pipeline: DatabaseRequestSecurityPipeline | None,
) -> None:
    pipeline = request_security_pipeline or _build_declared_request_security_pipeline(
        settings=settings,
        registry=registry,
        session_factory=app.state.session_factory,
    )
    if pipeline is None:
        return
    app.state.request_security_pipeline = pipeline
    app.state.request_security_resolver = pipeline.resolve
    app.state.route_authorizer = pipeline.authorize


def _build_declared_request_security_pipeline(
    *,
    settings: Settings,
    registry: AppRegistry,
    session_factory: async_sessionmaker[AsyncSession],
) -> DatabaseRequestSecurityPipeline | None:
    session_store_factory = _declared_auth_session_store_factory(registry)
    if session_store_factory is None:
        return None
    return DatabaseRequestSecurityPipeline(
        session_factory=session_factory,
        jwt_provider=LocalJwtProvider(LocalJwtConfig(secret=settings.security.jwt_secret)),
        session_store_factory=session_store_factory,
        tenant_lifecycle_policy=tenant_lifecycle_policy_from_settings(settings),
    )


def _declared_auth_session_store_factory(registry: AppRegistry) -> SessionStoreFactory | None:
    paths = [
        module.auth_session_store
        for module in registry.modules
        if module.auth_session_store is not None
    ]
    if not paths:
        return None
    if len(paths) > 1:
        raise ValueError("Only one app can declare auth_session_store")
    return _load_session_store_factory(paths[0])


def _load_session_store_factory(path: str) -> SessionStoreFactory:
    module_path, separator, attribute_name = path.rpartition(".")
    if not separator or not module_path or not attribute_name:
        raise ValueError(f"Invalid auth_session_store path: {path!r}")
    module = importlib.import_module(module_path)
    factory = getattr(module, attribute_name)
    if not callable(factory):
        raise TypeError(f"auth_session_store {path!r} must be callable")
    return factory


def _validate_installed_apps(installed_apps: list[str]) -> None:
    results = check_apps(installed_apps)
    failures = [result for result in results if not result.ok]
    if not failures:
        return
    details = "; ".join(_format_app_check_failure(result) for result in failures)
    raise ValueError(f"App conformance failed: {details}")


def _format_app_check_failure(result: AppCheckResult) -> str:
    return f"{result.module_path}: {', '.join(result.errors)}"


def _import_app_models(registry: AppRegistry) -> list[str]:
    imported: list[str] = []
    for module in registry.modules:
        for model_path in module.models:
            if model_path not in _IMPORTED_APP_MODEL_MODULES:
                importlib.import_module(model_path)
                _IMPORTED_APP_MODEL_MODULES.add(model_path)
            imported.append(model_path)
    return imported


def _assemble_app_runtime_registries(app: FastAPI, registry: AppRegistry) -> None:
    admin_registry = AdminRegistry.from_app_registry(registry)
    permission_registry = PermissionRegistry.from_app_registry(registry)
    migration_registry = MigrationRegistry.from_app_registry(registry)
    event_registry = EventRegistry.from_app_registry(registry)
    task_registry = TaskRegistry.from_app_registry(registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        registry,
        task_registry=task_registry,
    )

    if permission_registry.errors:
        raise ValueError("; ".join(permission_registry.errors))
    if migration_registry.errors:
        raise ValueError("; ".join(migration_registry.errors))

    app.state.admin_registry = admin_registry
    app.state.permission_registry = permission_registry
    app.state.migration_registry = migration_registry
    app.state.event_registry = event_registry
    app.state.task_registry = task_registry
    app.state.schedule_registry = schedule_registry
    app.include_router(build_admin_router(admin_registry))
