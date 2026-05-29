from core.auth.errors import invalid_auth_token
from core.auth.jwt_provider import LocalJwtConfig, LocalJwtProvider
from core.auth.oidc_provider import (
    ExternalAuthCallback,
    ExternalAuthIdentity,
    ExternalAuthState,
    ExternalAuthStateStore,
    HmacOidcIdTokenVerifier,
    MemoryExternalAuthStateStore,
    OidcClient,
    OidcIdTokenVerifier,
    OidcProviderAdapter,
    OidcProviderConfig,
    OidcTokenSet,
    OidcUserClaims,
    keycloak_oidc_provider_config,
    logto_oidc_provider_config,
)
from core.auth.schemas import CurrentUser, SessionPrincipal, TokenClaims
from core.auth.session import AuthSessionStore, AuthSessionValidator, StaticAuthSessionStore

__all__ = [
    "AuthSessionStore",
    "AuthSessionValidator",
    "CurrentUser",
    "ExternalAuthCallback",
    "ExternalAuthIdentity",
    "ExternalAuthState",
    "ExternalAuthStateStore",
    "HmacOidcIdTokenVerifier",
    "LocalJwtConfig",
    "LocalJwtProvider",
    "MemoryExternalAuthStateStore",
    "OidcClient",
    "OidcIdTokenVerifier",
    "OidcProviderAdapter",
    "OidcProviderConfig",
    "OidcTokenSet",
    "OidcUserClaims",
    "SessionPrincipal",
    "StaticAuthSessionStore",
    "TokenClaims",
    "invalid_auth_token",
    "keycloak_oidc_provider_config",
    "logto_oidc_provider_config",
]
