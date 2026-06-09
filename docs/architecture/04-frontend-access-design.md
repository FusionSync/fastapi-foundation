# Frontend Access Configuration Design

## Progress

- Status: `implemented_for_current_scope`
- Done: multi-agent review completed for mature IAM/RBAC models, frontend boundary, and FastAPI foundation fit.
- Done: confirmed that `tenant` is an authorization domain, not a role.
- Done: confirmed that menu, route, button and component visibility are frontend access decisions, not backend security boundaries.
- Done: confirmed that backend should not store frontend menu tree, layout, route component, icon, order or parent-child menu structure.
- Done: confirmed that this capability belongs in `platform_access` as a product-side IAM extension, not in `core.permissions`.
- Done: implemented frontend access mapping storage, validation, current-user evaluation APIs, permission specs, audit hooks and integration tests.
- Done: added a regression test proving frontend `access_key` cannot authorize backend routes.
- Done: `/me/access` supports tenant-bound console sessions and no-tenant platform bootstrap sessions.
- Done: `/me/access` returns mapping `version`, `policy_version`, `access_revision` and `evaluated_at` so clients do not treat mapping version as the full cache version.
- Next: expand with richer explanation output only when the console needs user-facing denial reasons.

## References

- NIST RBAC: users receive permissions through roles, and permissions are operations on protected objects.
  <https://csrc.nist.gov/projects/role-based-access-control>
- Casbin RBAC with Domains: tenant or organization is modeled as a domain where a user has roles.
  <https://casbin.org/docs/rbac-with-domains/>
- Auth0 Organizations: organization members can be assigned roles in that organization.
  <https://auth0.com/docs/manage-users/organizations/configure-organizations/add-member-roles>
- Keycloak Authorization Services: authorization separates resources, scopes, policies and permissions.
  <https://www.keycloak.org/docs/latest/authorization_services/>
- OWASP Authorization guidance: client-side visibility is not an access-control boundary.
  <https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>

## Boundary

Backend IAM remains the security boundary. It answers whether a subject can perform an action on a protected resource.

Frontend access configuration is a derived UX boundary. It answers whether the console should show a menu entry, route entry, button, action or component.

The backend stores and evaluates only this mapping:

```text
frontend access_key -> backend permission expression -> allowed/denied result
```

The backend must not store or own:

- menu parent-child structure;
- menu ordering;
- icons;
- frontend route paths;
- frontend component names;
- page layout;
- button position;
- copywriting used only by the frontend UI.

The frontend owns those layout and navigation details and references stable access keys.

## Core Concepts

### Tenant Is Not A Role

Correct model:

```text
User -> TenantMember -> Tenant
TenantMember/User -> RoleGrant in tenant domain
RoleGrant -> RoleTemplate -> Permissions
```

Examples:

```text
user-1 is a member of tenant-a
user-1 has tenant-admin role in tenant-a
tenant-admin grants tenant_member:manage and settings.tenant:manage
```

Incorrect model:

```text
tenant-a is a role
tenant-a-admin is a global role
```

That creates role explosion and mixes data isolation with authorization.

### Backend Permission

Backend permissions are security permissions used by route authorization and service authorization.

Examples:

```text
role_grant:read
role_grant:grant
role_grant:revoke
tenant_member:read
tenant_member:manage
settings.tenant:manage
file:download
```

These permissions are declared through `PermissionSpec`, assigned through `RoleTemplate` and `RoleGrant`, projected to `ProjectedPolicy`, and evaluated into `AuthorizationDecision`.

### Frontend Access Key

Frontend access keys are stable identifiers consumed by console UI code.

Examples:

```text
console.access.role_grants.page
console.access.role_grants.grant_button
console.tenants.members.page
console.tenants.members.invite_button
console.settings.tenant.page
console.files.download_action
```

They are not accepted by backend route authorization. Backend APIs continue to protect real operations with backend permissions.

## Module Ownership

### Keep In `core.permissions`

- `PermissionSpec`
- `RoleTemplate`
- `RoleGrant`
- `ProjectedPolicy`
- `AuthorizationDecision`
- route authorization dependency
- access context
- permission cache
- cross-tenant permission gate

`core.permissions` must not know about menu, button, page, route or `access_key`.

### Extend `platform_access`

Add frontend access configuration as a `platform_access` capability:

```text
platform_apps.access.models
platform_apps.access.schemas
platform_apps.access.services
platform_apps.access.router
platform_apps.access.permissions
```

Rationale: `platform_access` is already the IAM control plane. Frontend access mapping is IAM product configuration, but it is not core authorization infrastructure.

## Data Model

### FrontendAccessMapping

```text
id
client_id              console-web, admin-web, mobile-web
access_key             stable frontend key
owner_module           platform_access, platform_tenants, platform_files, app module label
evaluation_scope       tenant | platform
expression_json        permission expression
description
status                 active | disabled | deprecated
version
updated_by
reason
created_at
updated_at
```

Recommended uniqueness:

```text
unique(client_id, access_key)
```

### FrontendAccessMappingRevision

```text
id
mapping_id
client_id
access_key
old_expression_json
new_expression_json
old_status
new_status
version
changed_by
reason
created_at
```

The revision table is required because frontend access configuration changes can affect what users see in the console and must be auditable.

## Expression Grammar

Version 1 supports only permission expressions.

Allowed:

```json
{
  "all": [
    {"permission": "role_grant:read"},
    {"permission": "role_grant:grant"}
  ]
}
```

```json
{
  "any": [
    {"permission": "file:download"},
    {"permission": "file:read"}
  ]
}
```

```json
{
  "permission": "settings.tenant:manage"
}
```

Not allowed in version 1:

- scripts;
- arbitrary SQL;
- `not` expressions;
- role-name checks;
- tenant id checks supplied by frontend;
- frontend route path;
- component name;
- menu metadata;
- feature flag rollout rules.

Validation rules:

- every leaf permission must use `resource:action`;
- every leaf permission must exist in `PermissionRegistry`;
- tenant-scope mappings can only reference tenant or own/resource permissions;
- platform-scope mappings can only reference platform permissions;
- disabled mappings evaluate to denied;
- unknown mappings evaluate to denied.

## API Contract

### Platform Management APIs

These APIs manage frontend access mappings. They are platform administration APIs and must require platform-scope permissions.

```text
GET    /api/v1/platform/access/frontend-access?client_id=console-web
POST   /api/v1/platform/access/frontend-access
GET    /api/v1/platform/access/frontend-access/{access_key}?client_id=console-web
PATCH  /api/v1/platform/access/frontend-access/{access_key}?client_id=console-web
DELETE /api/v1/platform/access/frontend-access/{access_key}?client_id=console-web
GET    /api/v1/platform/access/frontend-access/{access_key}/history?client_id=console-web
POST   /api/v1/platform/access/frontend-access/validate
```

Create or update request:

```json
{
  "client_id": "console-web",
  "access_key": "console.access.role_grants.grant_button",
  "owner_module": "platform_access",
  "evaluation_scope": "tenant",
  "expression": {
    "permission": "role_grant:grant"
  },
  "description": "Show tenant role grant button.",
  "reason": "Initial console access setup"
}
```

### Current User APIs

These APIs return derived results for the current authenticated subject and current request tenant context.

```text
GET  /api/v1/me/access?client_id=console-web
POST /api/v1/me/access/check
```

`GET /me/access` response:

```json
{
  "client_id": "console-web",
  "tenant_id": "tenant-a",
  "version": 12,
  "policy_version": 7,
  "access_revision": "4f9a1b2c3d4e5f60718293ab",
  "evaluated_at": "2026-06-09T10:15:30.000000+00:00",
  "permissions": [
    "role_grant:read",
    "role_grant:grant"
  ],
  "access": {
    "console.access.role_grants.page": true,
    "console.access.role_grants.grant_button": true,
    "console.access.role_grants.revoke_button": false
  }
}
```

`POST /me/access/check` request:

```json
{
  "client_id": "console-web",
  "access_keys": [
    "console.access.role_grants.grant_button",
    "console.files.download_action"
  ]
}
```

Response:

```json
{
  "client_id": "console-web",
  "tenant_id": "tenant-a",
  "policy_version": 7,
  "access_revision": "4f9a1b2c3d4e5f60718293ab",
  "evaluated_at": "2026-06-09T10:15:30.000000+00:00",
  "results": [
    {
      "access_key": "console.access.role_grants.grant_button",
      "allowed": true,
      "reason": "matched_expression",
      "version": 3
    },
    {
      "access_key": "console.files.download_action",
      "allowed": false,
      "reason": "missing_permission",
      "version": 1
    }
  ]
}
```

## Relationship To Existing APIs

### `/me/permissions`

Existing API. It returns raw backend permissions for the current user and tenant.

Use cases:

- debugging;
- permission explanation;
- generic capability store;
- development tools.

### `/me/permissions/check`

Existing API. It checks raw backend permissions such as `role_grant:grant`.

Use cases:

- checking a backend permission directly;
- simple frontend cases that do not need configured access keys.

### `/me/access`

New API. It returns frontend access keys derived from frontend mappings, mapping status and raw backend permissions.

Use cases:

- menu filtering;
- route guard decisions;
- button visibility;
- component visibility;
- frontend console bootstrap.

For tenant-bound sessions, `tenant_id` is the current tenant and both tenant-scope and platform-scope mappings can be evaluated.
For no-tenant platform sessions, `tenant_id` is `null`; platform-scope mappings can be evaluated, while tenant-scope mappings return denied with `tenant_context_required`.

Cache contract:

- `version` is the maximum frontend mapping version for the client.
- `policy_version` is the maximum effective projected-policy version observed during evaluation.
- `access_revision` is a deterministic digest of mappings including status plus current effective permissions.
- `evaluated_at` is the server evaluation timestamp.

Frontend caches should use `access_revision` or a short TTL. They should not use `version` alone because role grants can change without frontend mappings changing.

Important rule:

```text
access_key must never be accepted by backend route authorization.
```

Backend routes continue to use:

```python
permissions=["role_grant:grant"]
```

not:

```python
permissions=["console.access.role_grants.grant_button"]
```

## Runtime Evaluation

Evaluation steps:

1. Resolve current user and optional current tenant from request context.
2. Load effective backend permissions from `ProjectedPolicy` for:
   - current tenant domain for tenant mappings;
   - `__platform__` domain for platform mappings.
3. Load `FrontendAccessMapping` rows for `client_id`, including disabled and deprecated mappings so status participates in the access result and revision.
4. Validate mapping expression version and shape.
5. Evaluate `all` or `any` in memory against the permission set.
6. Return boolean results and a coarse reason.

Reason values:

```text
matched_expression
missing_permission
unknown_access_key
disabled_mapping
invalid_mapping
tenant_context_required
```

Do not return sensitive internal policy details unless a separate explanation API is explicitly designed.

## Permission Points

Add platform permissions:

```text
access.frontend_config:read
access.frontend_config:manage
```

Recommended behavior:

- `read` protects platform management list/detail/history APIs.
- `manage` protects create/update/delete/validate APIs.
- `manage` requires `reason`.
- changes write audit records when `platform_audit` is installed.

Audit event names:

```text
frontend_access.mapping_created
frontend_access.mapping_updated
frontend_access.mapping_disabled
frontend_access.mapping_validated
```

## Frontend Usage

Frontend owns menu and route definitions:

```ts
const menus = [
  {
    title: "角色授权",
    path: "/access/role-grants",
    access: "console.access.role_grants.page",
  },
]
```

Frontend owns button definitions:

```ts
const actions = {
  grantRole: "console.access.role_grants.grant_button",
  revokeRole: "console.access.role_grants.revoke_button",
}
```

Frontend consumes backend access results:

```ts
if (access["console.access.role_grants.grant_button"]) {
  showGrantButton()
}
```

The backend still enforces the actual mutation API:

```text
POST /api/v1/access/role-grants
requires role_grant:grant
```

## Anti-Patterns

- Treating tenant as role.
- Checking role names directly in the frontend.
- Using menu or button visibility as API authorization.
- Storing menu layout, icon or frontend route metadata in IAM tables.
- Allowing arbitrary frontend-created permissions.
- Putting access keys into `core.permissions`.
- Returning all tenants' permissions in one token or one bootstrap response.
- Letting `/me/access` answer resource-instance authorization without loading the specific resource.

## TODO

- [x] Add `FrontendAccessMapping` and `FrontendAccessMappingRevision` models under `platform_apps.access`.
- [x] Add migration metadata for the new tables through the existing `platform_access` module metadata.
- [x] Add schemas for mapping create/update/read, revision read, validation, access result and access check.
- [x] Add expression parser and validator with `PermissionRegistry` checks.
- [x] Add `FrontendAccessConfigService` for CRUD, validation, revisions and audit.
- [x] Add `FrontendAccessEvaluationService` for current user batch evaluation.
- [x] Add platform management routers under `/platform/access/frontend-access`.
- [x] Add current user routers `GET /me/access` and `POST /me/access/check`.
- [x] Add `access.frontend_config:read` and `access.frontend_config:manage` permission specs.
- [x] Register routers and permissions in `platform_apps.access.module`.
- [x] Add audit records for mapping create/update/delete/validate when `platform_audit` is installed.
- [x] Add integration tests for tenant-scope access mappings.
- [ ] Add integration tests for platform-scope access mappings.
- [x] Add tests for unknown access keys.
- [ ] Add dedicated tests for disabled and invalid access keys.
- [x] Add tests proving backend route authorization never accepts `access_key`.
- [x] Update `docs/modules/platform-access.md` after implementation.
