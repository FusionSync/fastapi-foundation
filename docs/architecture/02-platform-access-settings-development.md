# Platform Access And Settings Development Plan

## Progress

- Status: `partial`
- Done: scope decision confirmed: build `platform_access` and `platform_settings`; defer billing, notifications and operations.
- Done: design boundary confirmed: `core` keeps framework primitives, `platform_apps` provide SaaS-facing management APIs.
- Done: references reviewed: Django auth permissions, FastAPI dependencies, Casbin RBAC with domains, Zanzibar, 12-Factor config, Kubernetes ConfigMap, OpenFeature and Unleash.
- Done: `core.permissions` now exposes `AccessContext`, `AuthorizationDecisionSet` and route decision selectors.
- Done: request security binds route authorization decisions into `AccessContext`.
- Done: `platform_access` now registers a standard app with permission catalog API and platform administrator grant API.
- Done: `core.apps` now supports typed `SettingSpec` declarations collected by `SettingRegistry`.
- Done: `platform_settings` now registers a standard app with typed definitions, DB-backed values, revision rows and platform/tenant/default resolution.
- Done: checkpoint tests cover AccessContext, settings registry, Access/Settings app conformance and API flows.
- Next:
  - Expand `platform_access` from platform-admin bootstrap into role template CRUD, tenant role grant CRUD and effective-permission explanations.
  - Add explicit `RequirePermission` / `RequirePlatformPermission` dependencies for dynamic authorization checks.
  - Add settings cache invalidation, audit records and stronger secret/redaction tests.
  - Add real migration manifests before treating Access/Settings as production-ready platform apps.

## Scope

This document defines the next platform app work for the FastAPI foundation:

```text
platform_access
  IAM management layer for role templates, role grants, platform admins and permission catalog.

platform_settings
  typed runtime configuration layer for platform and tenant overrides.
```

Out of scope for this phase:

```text
platform_billing
platform_notifications
platform_operations
```

## Design References

### IAM

- Django auth separates users, groups and permissions, and exposes permission checks such as `user.has_perm(...)`. It also supports object-specific permission customization and warns that permission caching can make immediate post-grant checks stale. Reference: <https://docs.djangoproject.com/en/5.2/topics/auth/default/>.
- FastAPI's dependency system is intended for shared logic, database connections and enforcing security/role requirements while minimizing repeated code. Reference: <https://fastapi.tiangolo.com/tutorial/dependencies/>.
- Casbin RBAC with domains maps naturally to SaaS tenants: one user can have different roles in different domains, and the matcher includes subject, domain, object and action. Reference: <https://casbin.apache.org/docs/rbac-with-domains/>.
- Zanzibar is a large-scale relationship-based authorization design. We should borrow the idea of explicit authorization facts and evaluated decisions, but not implement full ReBAC now. Reference: <https://research.google/pubs/zanzibar-googles-consistent-global-authorization-system/>.

### Settings

- 12-Factor config recommends environment variables for deploy-varying configuration. That remains `core.config`, not `platform_settings`. Reference: <https://12factor.net/config>.
- Kubernetes ConfigMap separates non-confidential configuration from container images and explicitly says confidential data belongs in Secret, not ConfigMap. Reference: <https://kubernetes.io/docs/concepts/configuration/configmap/>.
- OpenFeature defines evaluation context and context precedence for dynamic flag evaluation. We should borrow context/precedence ideas for future feature flags, not build a full flag platform in the first pass. Reference: <https://openfeature.dev/specification/sections/evaluation-context/>.
- Unleash models feature flags with type, environment, activation strategies, lifecycle and stale cleanup. We should borrow lifecycle discipline and naming conventions if settings later grows feature flags. Reference: <https://docs.getunleash.io/concepts/feature-flags>.

## Boundary

### Core

`core` should provide primitives that business apps can rely on without importing platform app internals:

- `AccessContext` ContextVar helpers.
- `AuthorizationDecisionSet` helpers for routes that require more than one permission proof.
- permission dependency factories for FastAPI routes.
- safe assertion helpers for service-layer authorization.
- `SettingSpec` and setting registry collection through `AppModule`.
- typed setting resolver interfaces.

`core` should not expose SaaS management APIs, role administration pages, or tenant-specific configuration CRUD.

### Platform Apps

`platform_access` and `platform_settings` provide product-facing APIs over core facts:

- API routes.
- request/response schemas.
- service orchestration.
- audit integration.
- outbox integration where projection or cache invalidation is needed.
- admin route metadata when admin registry is ready to consume it.

## Platform Access

### Current Facts

Existing implementation already has the hard parts:

```text
PermissionSpec
  app-level permission catalog item

RoleTemplate
  role definition with scope and permissions

RoleGrant
  source-of-truth assignment of a role to a subject in a tenant/domain

ProjectedPolicy
  executable policy projection

AuthorizationService
  runtime permission decision service

PLATFORM_TENANT_ID = "__platform__"
  fixed platform domain for platform administrators
```

This means `platform_access` should not introduce another user-role table or platform admin flag.

Current gaps:

- no platform-facing IAM management app;
- no role template CRUD;
- no tenant/platform role grant CRUD;
- no effective-permission or why-allowed explanation API;
- no access review API for stale, orphaned or high-risk grants;
- no helper to select a route authorization decision by `resource:action` when one route needs multiple decisions.

### Data Model

First pass uses existing core permission tables:

```text
RoleTemplate
  scope: tenant | platform
  name
  version
  permissions

RoleGrant
  tenant_id: tenant id or "__platform__"
  subject_type: user
  subject_id: global users.id
  role_template_id
  policy_version

ProjectedPolicy
  tenant_id
  subject
  resource
  action
  effect
  role_grant_id
```

Optional later model:

```text
AccessChangeRequest
  approval workflow for high-risk platform grants

ExternalGroupMapping
  OIDC group or SCIM group to RoleGrant mapping

ServiceAccount / ApiKeyCredential
  machine identity and scoped API credentials

AccessReviewCampaign
  periodic review of high-risk grants
```

Do not add it in the first pass.

### API

```text
GET    /api/v1/platform/access/permissions
GET    /api/v1/platform/access/role-templates
POST   /api/v1/platform/access/role-templates
PATCH  /api/v1/platform/access/role-templates/{role_template_id}
DELETE /api/v1/platform/access/role-templates/{role_template_id}

GET    /api/v1/platform/access/platform-admins
POST   /api/v1/platform/access/platform-admins
DELETE /api/v1/platform/access/platform-admins/{grant_id}

GET    /api/v1/access/role-grants
POST   /api/v1/access/role-grants
DELETE /api/v1/access/role-grants/{grant_id}

GET    /api/v1/me/permissions
POST   /api/v1/me/permissions/check

GET    /api/v1/platform/access/subjects/{subject_type}/{subject_id}/effective-permissions
POST   /api/v1/platform/access/check
POST   /api/v1/platform/access/reconcile
```

API permissions:

```text
access.permission.read      platform
access.role_template.read   platform
access.role_template.manage platform
access.platform_admin.read  platform
access.platform_admin.manage platform
access.role_grant.read      tenant
access.role_grant.grant     tenant
access.role_grant.revoke    tenant
access.effective.read       platform
access.check                platform
access.reconcile            platform
```

### FastAPI Integration

Keep the current route-level declaration as the default:

```python
router = create_router(
    "/platform/access/platform-admins",
    tags=["platform-access"],
    tenant_required=False,
    permission_scope="platform",
    permissions=("access.platform_admin:manage",),
)
```

Endpoint code should keep receiving the authorization proof explicitly:

```python
decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)]
```

For routes with multiple permissions, add a typed selector:

```python
invite_decision = Depends(route_authorization_decision_for("tenant_invitation:invite"))
role_grant_decision = Depends(route_authorization_decision_for("role_grant:grant"))
```

Add a convenience dependency for cases where a route needs an extra dynamic permission not declared on the router:

```python
RequirePermission("file", "delete")
RequirePlatformPermission("access.platform_admin", "manage")
```

The dependency should:

1. read `RequestContext`;
2. reuse existing route decisions if they match;
3. otherwise call `AuthorizationService`;
4. append the decision into `AccessContext`;
5. return `AuthorizationDecision`.

Service-layer code should still accept `AuthorizationDecision` explicitly. ContextVar helpers make request handlers less repetitive; they must not turn service authorization into hidden global state.

### AccessContext

Add a separate ContextVar, not new fields on `RequestContext`:

```python
@dataclass(frozen=True, slots=True)
class AccessContext:
    request_id: str
    user_id: str
    tenant_id: str | None
    decisions: tuple[AuthorizationDecision, ...] = ()
```

Public API:

```python
set_current_access(context)
get_current_access()
reset_current_access(token)
current_access()
append_access_decision(decision)
```

Add an immutable decision collection:

```python
@dataclass(frozen=True, slots=True)
class AuthorizationDecisionSet:
    decisions: tuple[AuthorizationDecision, ...]

    def require(self, permission: str, *, scope: str | None = None) -> AuthorizationDecision: ...
```

Rules:

- It stores decisions already made in this request; it does not store all user roles or all permissions.
- It must be reset in `try/finally`, same as `RequestContext`.
- It must not become a global permission cache. Permission cache remains `core.permissions.cache`.
- It can be used by audit, resource adapters and service assertions to avoid passing duplicate request metadata.

### Decorators

Decorator support should be limited and explicit:

```python
@requires_decision(resource="tenant_member", actions={"manage"})
async def update_member(..., authorization_decision: AuthorizationDecision):
    ...
```

This decorator only validates an already supplied decision. It should not open database sessions or perform hidden authorization queries. Hidden query decorators make transaction boundaries unclear and are harder to test.

### Services

```text
AccessCatalogService
  list registered PermissionSpec from PermissionRegistry

AccessRoleTemplateService
  create/update/delete templates after validating every permission against PermissionRegistry

AccessGrantService
  wrap RoleGrantService for tenant grants and platform grants; require actor, reason and AuthorizationDecision

PlatformAdminService
  grant/revoke platform administrator roles using tenant_id="__platform__"

EffectiveAccessService
  explain effective permissions from RoleGrant, RoleTemplate and ProjectedPolicy

AccessReviewService
  list orphaned, stale or high-risk grants for administrator review

AccessReconciliationService
  expose PolicyProjector reconcile and repair operations behind platform permission
```

### Audit

Must audit:

- role template creation/update/delete;
- tenant role grant/revoke;
- platform admin grant/revoke;
- permission projection repair;
- denied attempts for high-risk platform access changes.

### TODO

- [x] Add `core.permissions.context.AccessContext`.
- [x] Add `AuthorizationDecisionSet`.
- [x] Add `route_authorization_decision_for("resource:action")`.
- [x] Bind `AccessContext` in request security pipeline after authentication.
- [x] Append route authorization decisions into `AccessContext`.
- [ ] Add `RequirePermission` and `RequirePlatformPermission` dependency factories.
- [ ] Add `requires_decision` service assertion decorator.
- [x] Add `platform_apps.access` module skeleton.
- [ ] Add read-only APIs first: permission catalog, role templates, grants and effective permissions.
- [ ] Add role template schemas and service.
- [x] Add permission catalog API.
- [ ] Add tenant role grant API using existing `RoleGrantService`.
- [x] Add platform admin API using `RoleGrant` with `tenant_id="__platform__"`.
- [ ] Fix tenant invitation routes that need both `tenant_invitation:invite` and `role_grant:grant` decisions.
- [ ] Require a platform authorization decision for platform account user creation.
- [ ] Add reconcile API around existing policy projector.
- [ ] Add audit events and outbox/cache invalidation tests.
- [x] Add checkpoint tests proving platform admin is a RoleGrant, not a user flag.

## Platform Settings

### Why Not A Generic KV Store

A generic database KV table creates long-term problems:

- no type contract;
- no ownership by module;
- no migration path when a key changes;
- no permission model for sensitive values;
- no safe default when value is missing;
- no way to distinguish deploy config from tenant runtime config;
- no documentation or OpenAPI visibility.

`platform_settings` must be a typed registry plus scoped overrides.

### Core Versus Platform Settings

```text
core.config
  deploy-time infrastructure config
  environment/profile driven
  required before app startup
  examples: DATABASE__URL, SECURITY__JWT_SECRET_REF, DEPENDENCIES__REDIS_URL

platform_settings
  runtime product config
  database-backed overrides
  resolved after app startup
  examples: files.max_file_size_mb, auth.password_policy, tenancy.invitation_expire_hours
```

Infrastructure secrets stay in `core.config` and external secret providers. Runtime settings may reference secrets by `secret_ref`, but should not store secret plaintext.

### SettingSpec

Add a core spec collected by `AppModule`:

```python
@dataclass(frozen=True, slots=True)
class SettingSpec:
    module: str
    key: str
    value_type: Literal["string", "int", "float", "bool", "json", "enum", "string_list"]
    default: object
    scopes: tuple[Literal["platform", "tenant"], ...]
    category: str
    description: str
    required: bool = False
    runtime_mutable: bool = True
    sensitive: bool = False
    secret_ref_only: bool = False
    risk_level: Literal["low", "normal", "high", "critical"] = "normal"
    cache_ttl_seconds: int | None = None
    allowed_values: tuple[str, ...] = ()
    min_value: float | None = None
    max_value: float | None = None
    kind: Literal["config", "flag"] = "config"
    deprecated: bool = False
```

`AppModule` extension:

```python
settings: list[SettingSpec] = field(default_factory=list)
```

Registry rules:

- `(module, key)` must be unique.
- `module` must match app label or approved core category.
- `key` must use dotted lowercase names.
- every database value must reference a registered `SettingSpec`.
- sensitive setting values are not returned in plain API responses.
- `runtime_mutable=False` settings are exposed for diagnostics only and cannot be changed through `platform_settings`.
- defaults must pass the same type validation as database values.

### SettingValue

```text
SettingValue
  id
  module
  key
  scope              platform | tenant
  scope_id           "__platform__" or tenant_id
  value_json
  secret_ref
  value_type
  version
  status             active | disabled
  updated_by
  reason
  updated_at
```

Unique constraint:

```text
UNIQUE(module, key, scope, scope_id)
```

Do not put definitions in the database in the first pass. Definitions belong to code and app registration; values belong to the database.

High-risk settings should also write revision rows:

```text
SettingRevision
  setting_value_id
  old_value_json
  new_value_json
  old_secret_ref
  new_secret_ref
  changed_by
  reason
  created_at
```

Sensitive settings must store `secret_ref`, not plaintext. API responses return `configured`, `secret_ref`, version and timestamps, with value redacted.

### Module Categories

Initial first-pass categories:

```text
auth
accounts
tenancy
files
security
mq
tasks
ui
```

Conceptual categories:

```text
runtime_config
  deploy/process configuration mirrored for diagnostics only; not database-editable

platform_policy
  platform-wide business policy, such as lifecycle or file rules

app_setting
  dynamic setting owned by an app

feature_flag
  future flag category; boolean flags only in first pass

ui_preference
  low-risk UI preference; user scope can be added later

integration
  external service settings; credentials must use secret_ref
```

Initial examples:

```text
auth.password_min_length
auth.external_login_enabled
accounts.profile_update_enabled
tenancy.invitation_expire_hours
tenancy.allow_suspended_file_download
files.max_file_size_mb
files.allowed_extensions
security.csp_profile
mq.outbox_publish_enabled
tasks.default_retry_attempts
ui.brand_name
```

### Resolver

Resolution order:

```text
tenant override
  -> platform override
  -> registered default
```

For request-aware resolution, use current context:

```python
value = await settings.resolve("files.max_file_size_mb")
value = await settings.resolve("files.max_file_size_mb", tenant_id="tenant-a")
```

The resolver should support:

- typed return values;
- default fallback;
- explicit missing spec error;
- cache by `(module, key, scope, scope_id, version)`;
- cache invalidation after mutation;
- redacted diagnostics.

Resolver output should carry provenance:

```python
ResolvedSetting(
    value=...,
    source="tenant|platform|default",
    module="files",
    key="max_file_size_mb",
    value_version=3,
    reason="tenant_override",
)
```

Supported scopes in the first pass are `platform` and `tenant`. `user`, `resource` and profile-aware evaluation are later extensions; `profile` is diagnostic input from `core.config`, not a database-write scope.

### Feature Flags

Do not build a full feature flag service now.

Allow a future setting kind:

```text
kind = config | flag
```

First pass can support boolean flags as typed settings. Targeting, gradual rollout, variants and stale flag lifecycle should be deferred until there is a real product need.

If feature flags grow beyond boolean settings, introduce a provider interface compatible with OpenFeature concepts instead of overloading `SettingValue`.

### API

```text
GET   /api/v1/platform/settings/definitions
GET   /api/v1/platform/settings/values
PUT   /api/v1/platform/settings/values/{module}/{key}
DELETE /api/v1/platform/settings/values/{module}/{key}

GET   /api/v1/settings/values
PUT   /api/v1/settings/values/{module}/{key}
```

API permissions:

```text
settings.definition.read platform
settings.value.read      platform
settings.value.manage    platform
settings.tenant.read     tenant
settings.tenant.manage   tenant
```

### Audit

Must audit:

- setting value created;
- setting value changed;
- setting value deleted/reset;
- sensitive setting read denied;
- invalid key write attempt.

Payload should include:

```text
module
key
scope
scope_id
old_version
new_version
redacted_old_value
redacted_new_value
```

### TODO

- [x] Add `SettingSpec` to `core.apps`.
- [x] Add `SettingRegistry.from_app_registry()`.
- [x] Add conformance tests rejecting duplicate or invalid setting specs.
- [x] Add `platform_apps.settings` module skeleton.
- [x] Add `SettingValue` model and migration metadata.
- [x] Add `SettingRevision` for audited changes.
- [x] Add setting value schema validation and type validation.
- [x] Add resolver with platform/tenant/default precedence.
- [ ] Add cache and invalidation hooks.
- [x] Add `platform_settings.value_changed` event.
- [x] Add `secret_ref` handling and redacted response serialization.
- [x] Add platform settings APIs.
- [x] Add tenant settings APIs.
- [ ] Add audit for create/update/reset.
- [ ] Add docs for each built-in setting key.
- [ ] Add checkpoint tests proving arbitrary unregistered keys are rejected.
- [x] Add checkpoint tests proving tenant overrides win over platform overrides.

## Implementation Order

1. `core.permissions` access context and dependencies.
2. `platform_access` permission catalog and platform admin APIs.
3. `platform_access` role template and tenant role grant APIs.
4. `core.apps` setting spec and registry.
5. `platform_settings` resolver and model.
6. `platform_settings` platform and tenant APIs.
7. Cross-module checkpoint tests.

## Checkpoint Tests

```text
Access
  platform admin grant writes RoleGrant with tenant_id="__platform__"
  platform route authorizes only through platform RoleGrant projection
  tenant grant requires tenant-scoped decision
  role template cannot reference unregistered permissions
  revoke removes stale ProjectedPolicy and invalidates cache

Settings
  SettingRegistry rejects duplicate module/key definitions
  SettingValue rejects unregistered key
  tenant override wins over platform override
  platform override wins over registered default
  sensitive values are redacted in API and diagnostics
  setting mutation writes audit log
```

## Non-Goals

- Do not add `is_platform_admin` to user or current user objects.
- Do not store all user permissions in `AccessContext`.
- Do not create another role assignment table outside `RoleGrant`.
- Do not make `platform_settings` a generic untyped KV store.
- Do not store secret plaintext in runtime settings.
- Do not build feature flag targeting, billing, notification or operations UI in this phase.
