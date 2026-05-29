from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.cache.keys import (
    permission_cache_key,
    permission_role_grant_cache_key,
    permission_subject_cache_key,
    tenant_lifecycle_cache_key,
    tenant_membership_cache_key,
    tenant_settings_cache_key,
)
from core.cache.provider import CacheProvider

if TYPE_CHECKING:
    from core.events import EventEnvelope, EventRegistry

CacheKeyResolver = Callable[["EventEnvelope"], Sequence[str]]
ROLE_GRANT_CHANGED_EVENT = "permissions.role_grant_changed"
TENANT_MEMBER_ACTIVATED_EVENT = "tenant.member_activated"
TENANT_LIFECYCLE_EVENTS = (
    "tenant.created",
    "tenant.suspended",
    "tenant.reactivated",
    "tenant.deleting",
    "tenant.archived",
    "tenant.deleted",
)


@dataclass(frozen=True, slots=True)
class CacheInvalidationRule:
    event_type: str
    event_version: int
    keys_for_event: CacheKeyResolver
    reason: str

    def matches(self, envelope: EventEnvelope) -> bool:
        return (
            envelope.event_type == self.event_type
            and envelope.event_version == self.event_version
        )


@dataclass(frozen=True, slots=True)
class CacheInvalidationResult:
    event_type: str
    event_version: int
    matched_rules: int
    deleted_keys: tuple[str, ...]
    missing_keys: tuple[str, ...]


class CacheInvalidationHandler:
    def __init__(
        self,
        cache: CacheProvider,
        *,
        rules: Sequence[CacheInvalidationRule] | None = None,
    ) -> None:
        self.cache = cache
        self.rules = tuple(rules or default_cache_invalidation_rules())

    async def handle(self, envelope: EventEnvelope) -> CacheInvalidationResult:
        matched_rules = [rule for rule in self.rules if rule.matches(envelope)]
        keys = _dedupe(
            key
            for rule in matched_rules
            for key in rule.keys_for_event(envelope)
        )
        deleted: list[str] = []
        missing: list[str] = []
        for key in keys:
            if await self.cache.delete(key):
                deleted.append(key)
            else:
                missing.append(key)
        return CacheInvalidationResult(
            event_type=envelope.event_type,
            event_version=envelope.event_version,
            matched_rules=len(matched_rules),
            deleted_keys=tuple(deleted),
            missing_keys=tuple(missing),
        )


def default_cache_invalidation_rules() -> tuple[CacheInvalidationRule, ...]:
    tenant_rules = tuple(
        CacheInvalidationRule(
            event_type=event_type,
            event_version=1,
            keys_for_event=_tenant_lifecycle_keys,
            reason="tenant_lifecycle_changed",
        )
        for event_type in TENANT_LIFECYCLE_EVENTS
    )
    return (
        CacheInvalidationRule(
            event_type=ROLE_GRANT_CHANGED_EVENT,
            event_version=1,
            keys_for_event=_permission_role_grant_keys,
            reason="permission_facts_changed",
        ),
        CacheInvalidationRule(
            event_type=TENANT_MEMBER_ACTIVATED_EVENT,
            event_version=1,
            keys_for_event=_tenant_membership_keys,
            reason="tenant_membership_changed",
        ),
        *tenant_rules,
    )


def register_cache_invalidation_handlers(
    registry: EventRegistry,
    handler: CacheInvalidationHandler,
    *,
    rules: Sequence[CacheInvalidationRule] | None = None,
) -> None:
    for event_type, event_version in _event_pairs(rules or handler.rules):
        registry.register(
            event_type,
            event_version,
            handler.handle,
            handler_key=f"core.cache.invalidate:{event_type}:v{event_version}",
        )


def _permission_role_grant_keys(envelope: EventEnvelope) -> tuple[str, ...]:
    keys = [permission_cache_key(envelope.tenant_id)]
    grant_id = _payload_str(envelope, "grant_id")
    if grant_id is not None:
        keys.append(permission_role_grant_cache_key(envelope.tenant_id, grant_id))
    subject_type = _payload_str(envelope, "subject_type")
    subject_id = _payload_str(envelope, "subject_id")
    if subject_type is not None and subject_id is not None:
        keys.append(permission_subject_cache_key(envelope.tenant_id, subject_type, subject_id))
    return tuple(keys)


def _tenant_lifecycle_keys(envelope: EventEnvelope) -> tuple[str, ...]:
    return (
        tenant_settings_cache_key(envelope.tenant_id),
        tenant_lifecycle_cache_key(envelope.tenant_id),
        permission_cache_key(envelope.tenant_id),
    )


def _tenant_membership_keys(envelope: EventEnvelope) -> tuple[str, ...]:
    keys = [permission_cache_key(envelope.tenant_id)]
    user_id = _payload_str(envelope, "user_id")
    if user_id is not None:
        keys.append(tenant_membership_cache_key(envelope.tenant_id, user_id))
        keys.append(permission_subject_cache_key(envelope.tenant_id, "user", user_id))
    return tuple(keys)


def _payload_str(envelope: EventEnvelope, key: str) -> str | None:
    value = envelope.payload.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _dedupe(keys: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return tuple(deduped)


def _event_pairs(rules: Sequence[CacheInvalidationRule]) -> tuple[tuple[str, int], ...]:
    seen: set[tuple[str, int]] = set()
    pairs: list[tuple[str, int]] = []
    for rule in rules:
        pair = (rule.event_type, rule.event_version)
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
    return tuple(pairs)
