from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.cache import cache_key
from core.exceptions import AppError

QuotaScope = Literal["tenant", "user", "resource"]
_SCOPES = {"tenant", "user", "resource"}


@dataclass(frozen=True, slots=True)
class QuotaSubject:
    tenant_id: str
    user_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None

    def parts_for(self, scope: QuotaScope) -> tuple[str, ...]:
        self._require(self.tenant_id, "tenant_id")
        if scope == "tenant":
            return (f"tenant_id={self.tenant_id}",)
        if scope == "user":
            self._require(self.user_id, "user_id")
            return (f"tenant_id={self.tenant_id}", f"user_id={self.user_id}")
        self._require(self.resource_type, "resource_type")
        self._require(self.resource_id, "resource_id")
        return (
            f"tenant_id={self.tenant_id}",
            f"resource_type={self.resource_type}",
            f"resource_id={self.resource_id}",
        )

    def _require(self, value: str | None, field_name: str) -> None:
        if value is None or not value.strip():
            raise AppError(
                "VALIDATION_ERROR",
                f"Quota subject missing {field_name}",
                status_code=400,
            )


@dataclass(frozen=True, slots=True)
class QuotaRule:
    metric: str
    limit: int
    scope: QuotaScope = "tenant"

    def __post_init__(self) -> None:
        if not self.metric.strip() or ":" in self.metric:
            raise AppError(
                "VALIDATION_ERROR",
                "Quota metric must be non-empty and must not contain ':'",
                status_code=400,
            )
        if self.limit < 0:
            raise AppError(
                "VALIDATION_ERROR",
                "Quota limit must be greater than or equal to zero",
                status_code=400,
            )
        if self.scope not in _SCOPES:
            raise AppError(
                "VALIDATION_ERROR",
                "Quota scope is invalid",
                status_code=400,
            )

    def key_for(self, subject: QuotaSubject) -> str:
        return cache_key("quota", self.metric, *subject.parts_for(self.scope))


class QuotaRegistry:
    def __init__(self, *, tenant_id: str, rules: dict[str, QuotaRule]) -> None:
        if not tenant_id.strip():
            raise AppError("VALIDATION_ERROR", "Quota tenant_id is required", status_code=400)
        self.tenant_id = tenant_id
        self._rules = dict(rules)

    @classmethod
    def from_tenant_config(
        cls,
        *,
        tenant_id: str,
        config: dict[str, int | dict[str, Any]],
    ) -> QuotaRegistry:
        rules: dict[str, QuotaRule] = {}
        for metric, value in config.items():
            if isinstance(value, int):
                rules[metric] = QuotaRule(metric=metric, limit=value, scope="tenant")
                continue
            limit = value.get("limit")
            if not isinstance(limit, int):
                raise AppError(
                    "VALIDATION_ERROR",
                    f"Quota config for {metric!r} must declare integer limit",
                    status_code=400,
                )
            rules[metric] = QuotaRule(
                metric=metric,
                limit=limit,
                scope=value.get("scope", "tenant"),
            )
        return cls(tenant_id=tenant_id, rules=rules)

    def resolve(self, metric: str) -> QuotaRule:
        try:
            return self._rules[metric]
        except KeyError as exc:
            raise AppError(
                "VALIDATION_ERROR",
                f"No quota rule configured for metric: {metric}",
                status_code=400,
            ) from exc
