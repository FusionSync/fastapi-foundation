from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.cache import cache_key
from core.exceptions import AppError

RateLimitDimension = Literal["global", "tenant_id", "user_id", "ip_address", "route"]


@dataclass(frozen=True, slots=True)
class RateLimitIdentity:
    tenant_id: str | None = None
    user_id: str | None = None
    ip_address: str | None = None
    route: str | None = None

    def value_for(self, dimension: RateLimitDimension) -> str:
        if dimension == "global":
            return "all"
        value = getattr(self, dimension)
        if value is None or not value.strip():
            raise AppError(
                "VALIDATION_ERROR",
                f"Rate limit identity missing {dimension}",
                status_code=400,
            )
        return value


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    name: str
    limit: int
    window_seconds: int
    dimensions: tuple[RateLimitDimension, ...]
    fail_closed: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise AppError("VALIDATION_ERROR", "Rate limit rule name is required", status_code=400)
        if ":" in self.name:
            raise AppError(
                "VALIDATION_ERROR",
                "Rate limit rule name must not contain ':'",
                status_code=400,
            )
        if self.limit <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "Rate limit rule limit must be greater than zero",
                status_code=400,
            )
        if self.window_seconds <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "Rate limit rule window must be greater than zero",
                status_code=400,
            )
        if not self.dimensions:
            raise AppError(
                "VALIDATION_ERROR",
                "Rate limit rule must declare at least one dimension",
                status_code=400,
            )

    def key_for(self, identity: RateLimitIdentity) -> str:
        parts = [
            f"{dimension}={identity.value_for(dimension)}"
            for dimension in self.dimensions
        ]
        return cache_key("rate", self.name, *parts)


class RateLimitRegistry:
    def __init__(self, *, default_rule: RateLimitRule | None = None) -> None:
        self.default_rule = default_rule
        self._route_rules: dict[str, RateLimitRule] = {}

    def register_route(self, route: str, rule: RateLimitRule) -> None:
        if not route.strip():
            raise AppError("VALIDATION_ERROR", "Rate limit route is required", status_code=400)
        if route in self._route_rules:
            raise AppError(
                "VALIDATION_ERROR",
                f"Duplicate rate limit route: {route}",
                status_code=400,
            )
        self._route_rules[route] = rule

    def resolve(self, route: str) -> RateLimitRule:
        if route in self._route_rules:
            return self._route_rules[route]
        if self.default_rule is None:
            raise AppError(
                "VALIDATION_ERROR",
                f"No rate limit rule registered for route: {route}",
                status_code=400,
            )
        return self.default_rule

    def find(self, route: str) -> RateLimitRule | None:
        if route in self._route_rules:
            return self._route_rules[route]
        return self.default_rule
