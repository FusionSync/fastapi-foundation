from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DeploymentProfile = Literal["local", "private", "cloud"]
HardeningCategory = Literal["csp", "cookie", "tls", "headers"]


@dataclass(frozen=True, slots=True)
class SecurityHardeningItem:
    category: HardeningCategory
    control: str
    required: bool
    evidence: str

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "control": self.control,
            "required": self.required,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class SecurityHardeningChecklist:
    profile: DeploymentProfile
    items: tuple[SecurityHardeningItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "items": [item.to_dict() for item in self.items],
        }


def security_hardening_checklist(profile: DeploymentProfile) -> SecurityHardeningChecklist:
    if profile == "local":
        return SecurityHardeningChecklist(profile=profile, items=_local_items())
    if profile == "private":
        return SecurityHardeningChecklist(
            profile=profile,
            items=_production_items(tls_preload=False),
        )
    if profile == "cloud":
        return SecurityHardeningChecklist(
            profile=profile,
            items=_production_items(tls_preload=True),
        )
    raise ValueError(f"Unknown deployment profile: {profile}")


def _local_items() -> tuple[SecurityHardeningItem, ...]:
    return (
        SecurityHardeningItem(
            category="headers",
            control="Keep defensive response headers enabled during local smoke checks.",
            required=True,
            evidence=(
                "Content-Security-Policy, X-Content-Type-Options, X-Frame-Options, "
                "Referrer-Policy, Permissions-Policy"
            ),
        ),
    )


def _production_items(*, tls_preload: bool) -> tuple[SecurityHardeningItem, ...]:
    hsts = "Strict-Transport-Security: max-age=31536000; includeSubDomains"
    if tls_preload:
        hsts += "; preload"
    return (
        SecurityHardeningItem(
            category="csp",
            control="Emit a deny-by-default CSP for API and admin responses.",
            required=True,
            evidence=(
                "Content-Security-Policy: default-src 'self'; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'self'"
            ),
        ),
        SecurityHardeningItem(
            category="cookie",
            control="Set session cookies only with Secure, HttpOnly, and SameSite.",
            required=True,
            evidence=(
                "Cookie flags: Secure; HttpOnly; SameSite=Lax or Strict; no wildcard Domain"
            ),
        ),
        SecurityHardeningItem(
            category="tls",
            control="Terminate TLS before the API and enforce HTTPS at the edge.",
            required=True,
            evidence=hsts,
        ),
        SecurityHardeningItem(
            category="headers",
            control="Preserve all framework security headers through proxies and ingress.",
            required=True,
            evidence=(
                "X-Content-Type-Options, X-Frame-Options, Referrer-Policy, "
                "Permissions-Policy, Cross-Origin-Opener-Policy, "
                "X-Permitted-Cross-Domain-Policies"
            ),
        ),
    )
