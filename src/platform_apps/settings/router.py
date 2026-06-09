from __future__ import annotations

import importlib
from typing import Any

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
    SettingResetRead,
    SettingRevisionRead,
    SettingValueRead,
    SettingValueUpsertRequest,
    SettingValueValidateRead,
    SettingValueValidateRequest,
)
from platform_apps.settings.services import (
    SettingResolver,
    SettingValueService,
    setting_revision_to_dict,
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
platform_value_read_router = create_router(
    "/platform/settings/values",
    tags=["platform-settings"],
    tenant_required=False,
    permissions=["settings.value:read"],
    permission_scope="platform",
)
platform_resolve_router = create_router(
    "/platform/settings/resolve",
    tags=["platform-settings"],
    tenant_required=False,
    permissions=["settings.value:read"],
    permission_scope="platform",
)
platform_validate_router = create_router(
    "/platform/settings/validate",
    tags=["platform-settings"],
    tenant_required=False,
    permissions=["settings.value:manage"],
    permission_scope="platform",
    tenant_operation="write",
)
tenant_value_router = create_router(
    "/settings/values",
    tags=["tenant-settings"],
    permissions=["settings.tenant:manage"],
    tenant_operation="write",
)
tenant_value_read_router = create_router(
    "/settings/values",
    tags=["tenant-settings"],
    permissions=["settings.tenant:read"],
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


@platform_value_read_router.get("", response_model=ListEnvelope[SettingValueRead])
async def list_platform_setting_values(
    request: Request,
    scope: str = "platform",
    scope_id: str | None = None,
) -> dict[str, object]:
    resolved_scope, resolved_scope_id = _setting_scope(scope, scope_id)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        registry = _setting_registry(request)
        values = await SettingValueService(
            session,
            registry,
            _event_publisher(request, session),
        ).list_values(scope=resolved_scope, scope_id=resolved_scope_id)
        return ok_list(
            [_setting_value_read(registry, value) for value in values],
            Pagination(
                total=len(values),
                page=1,
                page_size=max(len(values), 1),
                has_next=False,
            ),
        )


@platform_value_read_router.get(
    "/{module}/{key:path}/history",
    response_model=ListEnvelope[SettingRevisionRead],
)
async def list_platform_setting_value_history(
    request: Request,
    module: str,
    key: str,
    scope: str = "platform",
    scope_id: str | None = None,
) -> dict[str, object]:
    resolved_scope, resolved_scope_id = _setting_scope(scope, scope_id)
    normalized_key = _setting_key(key)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        registry = _setting_registry(request)
        revisions = await SettingValueService(
            session,
            registry,
            _event_publisher(request, session),
        ).history(
            module=module,
            key=normalized_key,
            scope=resolved_scope,
            scope_id=resolved_scope_id,
        )
        spec = registry.get(module, normalized_key)
        return ok_list(
            [setting_revision_to_dict(revision, spec) for revision in revisions],
            Pagination(
                total=len(revisions),
                page=1,
                page_size=max(len(revisions), 1),
                has_next=False,
            ),
        )


@platform_value_read_router.get("/{module}/{key:path}", response_model=Envelope[SettingValueRead])
async def get_platform_setting_value(
    request: Request,
    module: str,
    key: str,
    scope: str = "platform",
    scope_id: str | None = None,
) -> dict[str, object]:
    resolved_scope, resolved_scope_id = _setting_scope(scope, scope_id)
    normalized_key = _setting_key(key)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        registry = _setting_registry(request)
        value = await SettingValueService(
            session,
            registry,
            _event_publisher(request, session),
        ).get_value(
            module=module,
            key=normalized_key,
            scope=resolved_scope,
            scope_id=resolved_scope_id,
        )
        return ok(_setting_value_read(registry, value))


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
            audit=_audit_recorder(request, session),
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
            expected_version=payload.expected_version,
        )
        return ok(_setting_value_read(registry, value))


@platform_value_router.delete("/{module}/{key:path}", response_model=Envelope[SettingResetRead])
async def reset_platform_setting_value(
    request: Request,
    module: str,
    key: str,
    scope: str = "platform",
    scope_id: str | None = None,
    reason: str | None = None,
    expected_version: int | None = None,
) -> dict[str, object]:
    context = _request_context()
    resolved_scope, resolved_scope_id = _setting_scope(scope, scope_id)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        setting = await SettingValueService(
            session,
            _setting_registry(request),
            _event_publisher(request, session),
            audit=_audit_recorder(request, session),
        ).reset(
            module=module,
            key=_setting_key(key),
            scope=resolved_scope,
            scope_id=resolved_scope_id,
            actor_id=context.user_id,
            request_id=context.request_id,
            reason=reason,
            expected_version=expected_version,
        )
        return ok(
            {
                "module": setting.module,
                "key": setting.key,
                "scope": setting.scope,
                "scope_id": setting.scope_id,
                "status": "reset",
                "version": setting.version,
            }
        )


@platform_validate_router.post(
    "/{module}/{key:path}",
    response_model=Envelope[SettingValueValidateRead],
)
async def validate_platform_setting_value(
    request: Request,
    module: str,
    key: str,
    payload: SettingValueValidateRequest,
) -> dict[str, object]:
    resolved_scope, resolved_scope_id = _setting_scope(payload.scope, payload.scope_id)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        registry = _setting_registry(request)
        result = await SettingValueService(
            session,
            registry,
            _event_publisher(request, session),
        ).validate(
            module=module,
            key=_setting_key(key),
            scope=resolved_scope,
            scope_id=resolved_scope_id,
            value=payload.value,
            secret_ref=payload.secret_ref,
        )
        return ok(result)


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


@tenant_value_read_router.get("", response_model=ListEnvelope[SettingValueRead])
async def list_tenant_setting_values(
    request: Request,
) -> dict[str, object]:
    context = _request_context()
    tenant_id = _current_tenant_id(context)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        registry = _setting_registry(request)
        values = await SettingValueService(
            session,
            registry,
            _event_publisher(request, session),
        ).list_values(scope="tenant", scope_id=tenant_id)
        return ok_list(
            [_setting_value_read(registry, value) for value in values],
            Pagination(
                total=len(values),
                page=1,
                page_size=max(len(values), 1),
                has_next=False,
            ),
        )


@tenant_value_router.put("/{module}/{key:path}", response_model=Envelope[SettingValueRead])
async def upsert_tenant_setting_value(
    request: Request,
    module: str,
    key: str,
    payload: SettingValueUpsertRequest,
) -> dict[str, object]:
    context = _request_context()
    tenant_id = _current_tenant_id(context)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        registry = _setting_registry(request)
        normalized_key = _setting_key(key)
        value = await SettingValueService(
            session,
            registry,
            _event_publisher(request, session),
            audit=_audit_recorder(request, session),
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
            expected_version=payload.expected_version,
        )
        return ok(_setting_value_read(registry, value))


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


def _audit_recorder(request: Request, session: AsyncSession) -> Any | None:
    registry = getattr(request.app.state, "app_registry", None)
    labels = {module.label for module in getattr(registry, "modules", [])}
    if "platform_audit" not in labels:
        return None
    public_api = importlib.import_module("platform_apps.audit.public_api")
    return public_api.AuditService(session)


def _request_context():
    context = get_current_context()
    if context is None or not context.user_id:
        raise AppError("AUTH_INVALID_TOKEN", "Authenticated user is required", status_code=401)
    return context


def _current_tenant_id(context: Any) -> str:
    tenant_id = getattr(context, "tenant_id", None)
    if not tenant_id:
        raise AppError("TENANT_ACCESS_DENIED", "Tenant context is required", status_code=403)
    return tenant_id


def _setting_key(value: str) -> str:
    return value.strip().replace("/", ".")


def _setting_scope(scope: str, scope_id: str | None) -> tuple[str, str]:
    resolved_scope = scope.strip()
    if resolved_scope == "platform":
        return resolved_scope, PLATFORM_TENANT_ID
    if resolved_scope == "tenant":
        if scope_id is None or not scope_id.strip():
            raise AppError(
                "VALIDATION_ERROR",
                "scope_id is required for tenant setting values",
                status_code=400,
            )
        return resolved_scope, scope_id.strip()
    raise AppError(
        "VALIDATION_ERROR",
        "setting scope must be platform or tenant",
        status_code=400,
    )


def _setting_value_read(registry: SettingRegistry, value: Any) -> dict[str, object]:
    return setting_value_to_dict(value, registry.get(value.module, value.key))
