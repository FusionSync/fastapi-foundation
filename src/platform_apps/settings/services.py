from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.apps import SettingSpec
from core.events import EventPublisher
from core.exceptions import AppError
from core.permissions import PLATFORM_TENANT_ID
from core.settings import SettingRegistry
from platform_apps.settings.models import SettingRevision, SettingValue

PLATFORM_SETTING_VALUE_CHANGED_EVENT = "platform_settings.value_changed"
_ACTIVE_STATUS = "active"
_SECRET_REDACTION = "***"


@dataclass(frozen=True, slots=True)
class ResolvedSetting:
    module: str
    key: str
    scope: str
    scope_id: str
    source: str
    value: Any | None
    version: int

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "key": self.key,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "source": self.source,
            "value": self.value,
            "version": self.version,
        }


class SettingResolver:
    def __init__(self, session: AsyncSession, registry: SettingRegistry) -> None:
        self.session = session
        self.registry = registry

    async def resolve(
        self,
        *,
        module: str,
        key: str,
        tenant_id: str | None = None,
    ) -> ResolvedSetting:
        spec = _registered_setting(self.registry, module=module, key=key)
        if tenant_id is not None and "tenant" in spec.scopes:
            tenant_value = await self._value(
                module=module,
                key=key,
                scope="tenant",
                scope_id=tenant_id,
            )
            if tenant_value is not None:
                return _resolved_from_row(tenant_value, source="tenant", spec=spec)

        platform_value = await self._value(
            module=module,
            key=key,
            scope="platform",
            scope_id=PLATFORM_TENANT_ID,
        )
        if platform_value is not None:
            return _resolved_from_row(platform_value, source="platform", spec=spec)

        scope = "tenant" if tenant_id is not None and "tenant" in spec.scopes else "platform"
        scope_id = tenant_id if scope == "tenant" and tenant_id is not None else PLATFORM_TENANT_ID
        return ResolvedSetting(
            module=module,
            key=key,
            scope=scope,
            scope_id=scope_id,
            source="default",
            value=_serialize_setting_value(spec, spec.default, secret_ref=None),
            version=0,
        )

    async def _value(
        self,
        *,
        module: str,
        key: str,
        scope: str,
        scope_id: str,
    ) -> SettingValue | None:
        result = await self.session.execute(
            select(SettingValue)
            .where(SettingValue.module == module)
            .where(SettingValue.key == key)
            .where(SettingValue.scope == scope)
            .where(SettingValue.scope_id == scope_id)
            .where(SettingValue.status == _ACTIVE_STATUS)
        )
        return result.scalars().first()


class SettingValueService:
    def __init__(
        self,
        session: AsyncSession,
        registry: SettingRegistry,
        events: EventPublisher,
    ) -> None:
        self.session = session
        self.registry = registry
        self.events = events

    async def upsert(
        self,
        *,
        module: str,
        key: str,
        scope: str,
        scope_id: str,
        value: Any | None,
        secret_ref: str | None,
        actor_id: str,
        request_id: str,
        reason: str | None = None,
    ) -> SettingValue:
        spec = _registered_setting(self.registry, module=module, key=key)
        _assert_setting_mutable(spec)
        _assert_scope_allowed(spec, scope=scope)
        resolved_value, resolved_secret_ref = _validated_setting_value(
            spec,
            value=value,
            secret_ref=secret_ref,
        )
        current = await self._current_value(
            module=module,
            key=key,
            scope=scope,
            scope_id=scope_id,
        )
        if current is None:
            current = SettingValue(
                module=module,
                key=key,
                scope=scope,
                scope_id=scope_id,
                value_json=resolved_value,
                secret_ref=resolved_secret_ref,
                value_type=spec.value_type,
                version=1,
                status=_ACTIVE_STATUS,
                updated_by=actor_id,
                reason=reason,
            )
            self.session.add(current)
            await self.session.flush()
            self._add_revision(
                current,
                old_value=None,
                new_value=resolved_value,
                old_secret_ref=None,
                new_secret_ref=resolved_secret_ref,
                actor_id=actor_id,
                reason=reason,
            )
        else:
            old_value = current.value_json
            old_secret_ref = current.secret_ref
            current.value_json = resolved_value
            current.secret_ref = resolved_secret_ref
            current.value_type = spec.value_type
            current.version += 1
            current.status = _ACTIVE_STATUS
            current.updated_by = actor_id
            current.reason = reason
            self._add_revision(
                current,
                old_value=old_value,
                new_value=resolved_value,
                old_secret_ref=old_secret_ref,
                new_secret_ref=resolved_secret_ref,
                actor_id=actor_id,
                reason=reason,
            )
        await self.session.flush()
        await self.events.publish(
            event_type=PLATFORM_SETTING_VALUE_CHANGED_EVENT,
            aggregate_type="setting_value",
            aggregate_id=current.id,
            tenant_id=scope_id,
            payload={
                "tenant_id": scope_id,
                "actor_id": actor_id,
                "request_id": request_id,
                "setting_value_id": current.id,
                "module": module,
                "key": key,
                "scope": scope,
                "scope_id": scope_id,
                "version": current.version,
            },
        )
        return current

    async def _current_value(
        self,
        *,
        module: str,
        key: str,
        scope: str,
        scope_id: str,
    ) -> SettingValue | None:
        result = await self.session.execute(
            select(SettingValue)
            .where(SettingValue.module == module)
            .where(SettingValue.key == key)
            .where(SettingValue.scope == scope)
            .where(SettingValue.scope_id == scope_id)
        )
        return result.scalars().first()

    def _add_revision(
        self,
        setting: SettingValue,
        *,
        old_value: Any | None,
        new_value: Any | None,
        old_secret_ref: str | None,
        new_secret_ref: str | None,
        actor_id: str,
        reason: str | None,
    ) -> None:
        self.session.add(
            SettingRevision(
                setting_value_id=setting.id,
                module=setting.module,
                key=setting.key,
                scope=setting.scope,
                scope_id=setting.scope_id,
                old_value_json=old_value,
                new_value_json=new_value,
                old_secret_ref=old_secret_ref,
                new_secret_ref=new_secret_ref,
                version=setting.version,
                changed_by=actor_id,
                reason=reason,
            )
        )


def setting_value_to_dict(setting: SettingValue, spec: SettingSpec) -> dict[str, object]:
    return {
        "id": setting.id,
        "module": setting.module,
        "key": setting.key,
        "scope": setting.scope,
        "scope_id": setting.scope_id,
        "value": _serialize_setting_value(
            spec,
            setting.value_json,
            secret_ref=setting.secret_ref,
        ),
        "secret_ref": _serialize_secret_ref(spec, setting.secret_ref),
        "value_type": setting.value_type,
        "version": setting.version,
        "status": setting.status,
        "updated_by": setting.updated_by,
        "reason": setting.reason,
    }


def _registered_setting(
    registry: SettingRegistry,
    *,
    module: str,
    key: str,
) -> SettingSpec:
    try:
        return registry.get(module, key)
    except KeyError as exc:
        raise AppError(
            "VALIDATION_ERROR",
            f"Unknown setting: {module}.{key}",
            status_code=400,
        ) from exc


def _assert_setting_mutable(spec: SettingSpec) -> None:
    if not spec.runtime_mutable:
        raise AppError(
            "VALIDATION_ERROR",
            f"Setting {spec.full_key} is not runtime mutable",
            status_code=400,
        )


def _assert_scope_allowed(spec: SettingSpec, *, scope: str) -> None:
    if scope not in spec.scopes:
        raise AppError(
            "VALIDATION_ERROR",
            f"Setting {spec.full_key} does not support {scope} scope",
            status_code=400,
        )


def _validated_setting_value(
    spec: SettingSpec,
    *,
    value: Any | None,
    secret_ref: str | None,
) -> tuple[Any | None, str | None]:
    if spec.secret_ref_only:
        if not isinstance(secret_ref, str) or not secret_ref.strip():
            raise AppError(
                "VALIDATION_ERROR",
                f"Setting {spec.full_key} requires secret_ref",
                status_code=400,
            )
        return None, secret_ref.strip()
    if value is None:
        raise AppError(
            "VALIDATION_ERROR",
            f"Setting {spec.full_key} requires value",
            status_code=400,
        )
    try:
        return spec.validate_value(value), secret_ref.strip() if secret_ref else None
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", str(exc), status_code=400) from exc


def _resolved_from_row(
    setting: SettingValue,
    *,
    source: str,
    spec: SettingSpec,
) -> ResolvedSetting:
    return ResolvedSetting(
        module=setting.module,
        key=setting.key,
        scope=setting.scope,
        scope_id=setting.scope_id,
        source=source,
        value=_serialize_setting_value(spec, setting.value_json, secret_ref=setting.secret_ref),
        version=setting.version,
    )


def _serialize_setting_value(
    spec: SettingSpec,
    value: Any | None,
    *,
    secret_ref: str | None,
) -> Any | None:
    if spec.sensitive or spec.secret_ref_only:
        return _SECRET_REDACTION if value is not None or secret_ref is not None else None
    return value


def _serialize_secret_ref(spec: SettingSpec, secret_ref: str | None) -> str | None:
    if secret_ref is None:
        return None
    return _SECRET_REDACTION if spec.sensitive or spec.secret_ref_only else secret_ref


__all__ = [
    "PLATFORM_SETTING_VALUE_CHANGED_EVENT",
    "ResolvedSetting",
    "SettingResolver",
    "SettingValueService",
    "setting_value_to_dict",
]
