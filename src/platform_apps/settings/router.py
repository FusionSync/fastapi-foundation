from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.base import create_router
from core.context import get_current_context
from core.db import unit_of_work
from core.events import EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxEventPublisher, OutboxRepository
from core.permissions import PLATFORM_TENANT_ID
from core.serialization import Envelope, ListEnvelope, Pagination, ok, ok_list
from core.settings import SettingRegistry
from platform_apps.settings.schemas import (
    ResolvedSettingRead,
    SettingDefinitionRead,
    SettingValueRead,
    SettingValueUpsertRequest,
)
from platform_apps.settings.services import (
    SettingResolver,
    SettingValueService,
    setting_value_to_dict,
)

definition_router = create_router(
    "/platform/settings/definitions",
    tags=["platform-settings"],
    tenant_required=False,
    permissions=["settings.definition:read"],
    permission_scope="platform",
)
platform_value_router = create_router(
    "/platform/settings/values",
    tags=["platform-settings"],
    tenant_required=False,
    permissions=["settings.value:manage"],
    permission_scope="platform",
    tenant_operation="write",
)
platform_resolve_router = create_router(
    "/platform/settings/resolve",
    tags=["platform-settings"],
    tenant_required=False,
    permissions=["settings.value:read"],
    permission_scope="platform",
)
tenant_value_router = create_router(
    "/tenants/{tenant_id}/settings/values",
    tags=["tenant-settings"],
    permissions=["settings.tenant:manage"],
    tenant_operation="write",
)

router = definition_router


@definition_router.get("", response_model=ListEnvelope[SettingDefinitionRead])
async def list_setting_definitions(request: Request) -> dict[str, object]:
    registry = _setting_registry(request)
    items = [
        setting.to_dict()
        for setting in sorted(
            registry.settings,
            key=lambda item: (item.spec.module, item.spec.key),
        )
    ]
    return ok_list(
        items,
        Pagination(
            total=len(items),
            page=1,
            page_size=max(len(items), 1),
            has_next=False,
        ),
    )


@platform_value_router.put("/{module}/{key:path}", response_model=Envelope[SettingValueRead])
async def upsert_platform_setting_value(
    request: Request,
    module: str,
    key: str,
    payload: SettingValueUpsertRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        registry = _setting_registry(request)
        normalized_key = _setting_key(key)
        value = await SettingValueService(
            session,
            registry,
            _event_publisher(request, session),
        ).upsert(
            module=module,
            key=normalized_key,
            scope="platform",
            scope_id=PLATFORM_TENANT_ID,
            value=payload.value,
            secret_ref=payload.secret_ref,
            actor_id=context.user_id,
            request_id=context.request_id,
            reason=payload.reason,
        )
        spec = registry.get(module, normalized_key)
        return ok(setting_value_to_dict(value, spec))


@platform_resolve_router.get("/{module}/{key:path}", response_model=Envelope[ResolvedSettingRead])
async def resolve_setting_value(
    request: Request,
    module: str,
    key: str,
    tenant_id: str | None = None,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        resolved = await SettingResolver(session, _setting_registry(request)).resolve(
            module=module,
            key=_setting_key(key),
            tenant_id=tenant_id,
        )
        return ok(resolved.to_dict())


@tenant_value_router.put("/{module}/{key:path}", response_model=Envelope[SettingValueRead])
async def upsert_tenant_setting_value(
    request: Request,
    tenant_id: str,
    module: str,
    key: str,
    payload: SettingValueUpsertRequest,
) -> dict[str, object]:
    context = _request_context()
    if context.tenant_id != tenant_id:
        raise AppError(
            "TENANT_CONTEXT_CONFLICT",
            "Tenant path must match request tenant context",
            status_code=403,
        )
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        registry = _setting_registry(request)
        normalized_key = _setting_key(key)
        value = await SettingValueService(
            session,
            registry,
            _event_publisher(request, session),
        ).upsert(
            module=module,
            key=normalized_key,
            scope="tenant",
            scope_id=tenant_id,
            value=payload.value,
            secret_ref=payload.secret_ref,
            actor_id=context.user_id,
            request_id=context.request_id,
            reason=payload.reason,
        )
        spec = registry.get(module, normalized_key)
        return ok(setting_value_to_dict(value, spec))


def _session_factory(request: Request):
    return request.app.state.session_factory


def _active_session(session: AsyncSession | None) -> AsyncSession:
    if session is None:
        raise AppError("SYSTEM_ERROR", "Database session is not available", status_code=500)
    return session


def _setting_registry(request: Request) -> SettingRegistry:
    registry = getattr(request.app.state, "setting_registry", None)
    if not isinstance(registry, SettingRegistry):
        raise AppError("SYSTEM_ERROR", "Setting registry is not configured", status_code=500)
    return registry


def _event_publisher(request: Request, session: AsyncSession) -> OutboxEventPublisher:
    registry = getattr(request.app.state, "event_registry", None)
    if registry is not None and not isinstance(registry, EventRegistry):
        raise AppError("SYSTEM_ERROR", "Event registry is invalid", status_code=500)
    return OutboxEventPublisher(OutboxRepository(session, registry=registry))


def _request_context():
    context = get_current_context()
    if context is None or not context.user_id:
        raise AppError("AUTH_INVALID_TOKEN", "Authenticated user is required", status_code=401)
    return context


def _setting_key(value: str) -> str:
    return value.strip().replace("/", ".")
