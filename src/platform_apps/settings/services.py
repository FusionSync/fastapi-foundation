from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.apps import SettingSpec
from core.audit import AuditRecorder
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
        *,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.events = events
        self.audit = audit

    async def list_values(
        self,
        *,
        scope: str,
        scope_id: str,
    ) -> list[SettingValue]:
        result = await self.session.execute(
            select(SettingValue)
            .where(SettingValue.scope == scope)
            .where(SettingValue.scope_id == scope_id)
            .where(SettingValue.status == _ACTIVE_STATUS)
            .order_by(SettingValue.module.asc(), SettingValue.key.asc())
        )
        return list(result.scalars().all())

    async def get_value(
        self,
        *,
        module: str,
        key: str,
        scope: str,
        scope_id: str,
    ) -> SettingValue:
        spec = _registered_setting(self.registry, module=module, key=key)
        _assert_scope_allowed(spec, scope=scope)
        current = await self._active_value(
            module=module,
            key=key,
            scope=scope,
            scope_id=scope_id,
        )
        if current is None:
            raise AppError(
                "NOT_FOUND",
                f"Setting value {module}.{key} was not found",
                status_code=404,
            )
        return current

    async def history(
        self,
        *,
        module: str,
        key: str,
        scope: str,
        scope_id: str,
    ) -> list[SettingRevision]:
        spec = _registered_setting(self.registry, module=module, key=key)
        _assert_scope_allowed(spec, scope=scope)
        result = await self.session.execute(
            select(SettingRevision)
            .where(SettingRevision.module == module)
            .where(SettingRevision.key == key)
            .where(SettingRevision.scope == scope)
            .where(SettingRevision.scope_id == scope_id)
            .order_by(SettingRevision.version.asc(), SettingRevision.id.asc())
        )
        return list(result.scalars().all())

    async def validate(
        self,
        *,
        module: str,
        key: str,
        scope: str,
        scope_id: str,
        value: Any | None,
        secret_ref: str | None,
    ) -> dict[str, object]:
        spec = _registered_setting(self.registry, module=module, key=key)
        _assert_setting_mutable(spec)
        _assert_scope_allowed(spec, scope=scope)
        resolved_value, resolved_secret_ref = _validated_setting_value(
            spec,
            value=value,
            secret_ref=secret_ref,
        )
        return {
            "module": module,
            "key": key,
            "scope": scope,
            "scope_id": scope_id,
            "value": _serialize_setting_value(
                spec,
                resolved_value,
                secret_ref=resolved_secret_ref,
            ),
            "secret_ref": _serialize_secret_ref(spec, resolved_secret_ref),
            "value_type": spec.value_type,
            "valid": True,
            "dry_run": True,
        }

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
        expected_version: int | None = None,
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
        _assert_expected_version(current, expected_version=expected_version)
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
        await self._record_audit(
            setting=current,
            action=PLATFORM_SETTING_VALUE_CHANGED_EVENT,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            payload={
                "module": module,
                "key": key,
                "scope": scope,
                "scope_id": scope_id,
                "version": current.version,
                "status": current.status,
            },
        )
        return current

    async def reset(
        self,
        *,
        module: str,
        key: str,
        scope: str,
        scope_id: str,
        actor_id: str,
        request_id: str,
        reason: str | None = None,
        expected_version: int | None = None,
    ) -> SettingValue:
        spec = _registered_setting(self.registry, module=module, key=key)
        _assert_setting_mutable(spec)
        _assert_scope_allowed(spec, scope=scope)
        current = await self._active_value(
            module=module,
            key=key,
            scope=scope,
            scope_id=scope_id,
        )
        if current is None:
            raise AppError(
                "NOT_FOUND",
                f"Setting value {module}.{key} was not found",
                status_code=404,
            )
        _assert_expected_version(current, expected_version=expected_version)
        old_value = current.value_json
        old_secret_ref = current.secret_ref
        current.value_json = None
        current.secret_ref = None
        current.version += 1
        current.status = "deleted"
        current.updated_by = actor_id
        current.reason = reason
        self._add_revision(
            current,
            old_value=old_value,
            new_value=None,
            old_secret_ref=old_secret_ref,
            new_secret_ref=None,
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
                "status": current.status,
            },
        )
        await self._record_audit(
            setting=current,
            action=PLATFORM_SETTING_VALUE_CHANGED_EVENT,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            payload={
                "module": module,
                "key": key,
                "scope": scope,
                "scope_id": scope_id,
                "version": current.version,
                "status": current.status,
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

    async def _active_value(
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

    async def _record_audit(
        self,
        *,
        setting: SettingValue,
        action: str,
        actor_id: str,
        request_id: str,
        reason: str | None,
        payload: dict[str, object],
    ) -> None:
        if self.audit is None:
            return
        await self.audit.record(
            action=action,
            resource_type="setting_value",
            resource_id=setting.id,
            result="success",
            tenant_id=None if setting.scope == "platform" else setting.scope_id,
            actor_id=actor_id,
            reason=reason,
            policy_version=setting.version,
            request_id=request_id,
            payload=payload,
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


def setting_revision_to_dict(
    revision: SettingRevision,
    spec: SettingSpec,
) -> dict[str, object]:
    return {
        "id": revision.id,
        "setting_value_id": revision.setting_value_id,
        "module": revision.module,
        "key": revision.key,
        "scope": revision.scope,
        "scope_id": revision.scope_id,
        "old_value": _serialize_setting_value(
            spec,
            revision.old_value_json,
            secret_ref=revision.old_secret_ref,
        ),
        "new_value": _serialize_setting_value(
            spec,
            revision.new_value_json,
            secret_ref=revision.new_secret_ref,
        ),
        "old_secret_ref": _serialize_secret_ref(spec, revision.old_secret_ref),
        "new_secret_ref": _serialize_secret_ref(spec, revision.new_secret_ref),
        "version": revision.version,
        "changed_by": revision.changed_by,
        "reason": revision.reason,
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


def _assert_expected_version(
    current: SettingValue | None,
    *,
    expected_version: int | None,
) -> None:
    if expected_version is None:
        return
    if expected_version < 0:
        raise AppError(
            "VALIDATION_ERROR",
            "expected_version must not be negative",
            status_code=400,
        )
    current_version = current.version if current is not None else 0
    if current_version != expected_version:
        raise AppError(
            "CONFLICT",
            "Setting value version conflict",
            status_code=409,
            details={
                "expected_version": expected_version,
                "current_version": current_version,
            },
        )


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
    "setting_revision_to_dict",
    "setting_value_to_dict",
]
