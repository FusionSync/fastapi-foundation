# Platform Access

## Progress

- Status: `partial`
- Done: standard `platform_access` app module is registered with permissions, routes, models metadata, migrations metadata and public API.
- Done: permission catalog API reads the runtime `PermissionRegistry`.
- Done: platform administrator grant API writes `RoleGrant` rows in the `__platform__` domain instead of adding a user flag.
- Done: route authorization uses `access.permission:read` and `access.platform_admin:manage` with platform scope.
- Done: role template list/create/update APIs validate declared permissions against `PermissionRegistry`.
- Done: tenant role grant list/grant/revoke APIs reuse core `RoleGrantService` facts and immediately project grants to `ProjectedPolicy`.
- Done: effective-permission query and projection reconcile/repair API are exposed through the platform control plane.
- Done: tenant route permissions now use the same `role_grant:*` resource as the core service-layer authorization checks.
- Done: `permissions.role_grant_changed` handler is registered by the app module so outbox dispatch projects grants without manual test wiring.
- Done: high-risk IAM mutations require `reason`, reject duplicate tenant grants, and write audit logs when `platform_audit` is installed.
- Done: first platform admin can be bootstrapped with `core permissions bootstrap-platform-admin` without adding user flags.
- Done: frontend access mappings provide `access_key -> permission expression` storage and `/me/access` evaluation without owning frontend menu layout.
- Done: frontend access keys are explicitly kept out of backend route authorization; backend APIs still enforce `resource:action` permissions.
- Next: expand access management to external identities, service-account administration and user-facing permission explanations.

## Design Notes

`platform_access` is the SaaS IAM control plane. It is intentionally built on top of core permission facts:

```text
PermissionSpec -> RoleTemplate -> RoleGrant -> ProjectedPolicy -> AuthorizationDecision
```

This follows the same split used by mature authorization systems: Django keeps permissions separate from users, Casbin models tenant/domain-aware RBAC, and Zanzibar-style systems treat authorization facts as durable inputs to evaluated decisions. The foundation does not add `is_platform_admin` to `users`.

## Bootstrap

First platform administrator setup is a CLI-only flow:

```bash
core permissions bootstrap-platform-admin \
  --database-url sqlite+aiosqlite:///./data/local.db \
  --user-id admin-1 \
  --installed-app platform_apps.access.module \
  --json
```

The command only runs while the `__platform__` domain has no platform `RoleGrant`.
It creates a default `platform-admin` role template from registered platform permissions,
grants it to the global user id, and immediately writes `ProjectedPolicy` rows.

## Frontend Access

`platform_access` stores optional frontend access mappings for console clients:

```text
frontend access_key -> backend permission expression -> allowed/denied result
```

The frontend still owns menu trees, route paths, icons, ordering, component names and button placement. The backend only stores stable `access_key` mappings and evaluates them against the current user's projected backend permissions.

`/me/access` supports both current-tenant console sessions and no-tenant platform bootstrap sessions. No-tenant platform sessions evaluate platform-scope mappings and deny tenant-scope mappings with `tenant_context_required`.

The response includes `version`, `policy_version`, `access_revision` and `evaluated_at`. Frontend caches should not use `version` alone because it only represents frontend mapping versions; role grants can change without changing mappings.

Current-user APIs:

```text
GET  /api/v1/me/access?client_id=console-web
POST /api/v1/me/access/check
```

Platform management APIs:

```text
GET    /api/v1/platform/access/frontend-access
POST   /api/v1/platform/access/frontend-access
GET    /api/v1/platform/access/frontend-access/{access_key}
PATCH  /api/v1/platform/access/frontend-access/{access_key}
DELETE /api/v1/platform/access/frontend-access/{access_key}
GET    /api/v1/platform/access/frontend-access/{access_key}/history
POST   /api/v1/platform/access/frontend-access/validate
```

Backend security rule:

```text
access_key is never accepted by route authorization.
```

Routes and services continue to enforce backend permissions such as `role_grant:grant`, `tenant_member:manage` or `settings.tenant:manage`.

## TODO

- [x] Register `platform_access` as a standard AppModule.
- [x] Add permission catalog API.
- [x] Add platform administrator grant API.
- [x] Cover platform admin grants with integration tests.
- [x] Align tenant role-grant route permissions with the existing `role_grant:*` service-layer authorization resource.
- [x] Register the `permissions.role_grant_changed` handler from the app registry so grants project to `ProjectedPolicy` without manual test wiring.
- [x] Add role template list/create/update APIs with PermissionRegistry validation.
- [x] Add tenant role grant list/grant/revoke APIs using the existing RoleGrant facts.
- [x] Add effective permission explanation APIs.
- [x] Add projection reconcile/repair API around `PolicyProjector.reconcile()`.
- [x] Add audit events for high-risk IAM mutations.
- [x] Require reason for platform admin and role grant mutations.
- [x] Add first platform admin bootstrap CLI.
- [x] Add IAM checkpoint tests for projection and tenant route authorization.
- [x] Add IAM checkpoint tests for outbox projection and duplicate grants.
- [x] Add frontend access mapping storage and current-user evaluation APIs.
- [x] Add tests proving frontend access keys cannot bypass backend route authorization.
