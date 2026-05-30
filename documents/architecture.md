# Architecture for operators and app developers

This framework uses a strict runtime core plus pluggable APP modules.
Business teams extend the platform with APP modules, while runtime concerns stay in core.

## Runtime architecture

1. `core.config.get_settings()` loads configuration from environment variables.
2. `AppRegistry` loads `installed_apps` and validates module dependencies and compatibility.
3. `core.app.factory.create_app` assembles middleware, system routes, and app runtime registries.
4. Request security pipeline resolves token session and tenant context.
5. Permissions are checked against route policy and role projection.
6. Process role starts selected runtime loops (`server`, `worker`, `scheduler`, `outbox-dispatcher`, `migrate`).

### Process role behavior

- `server`: API traffic entrypoint; exposes `/healthz`, `/readyz`, `/version`, `/docs`, `/metrics`.
- `worker`: consumes async task queue and executes jobs.
- `scheduler`: triggers scheduled jobs.
- `outbox-dispatcher`: drains event outbox in batches.
- `migrate`: runs staged migration planning and execution workflows.

## APP module contract

Every APP module must export `module` as `AppModule`:

```python
from core.apps import AppModule, MigrationSpec

module = AppModule(
    label="books",
    version="0.1.0",
    routers=[...],
    models=["platform_apps.books.models"],
    migrations=MigrationSpec(path="platform_apps.books.migrations"),
    permissions=[...],
)
```

Mandatory structure:

- `label` must be valid snake_case.
- `version` must be declared.
- `routers`, `models`, `permissions` must be valid values.
- `migrations` must be importable and point to a `manifest`.

Optional fields enable extension:

- `dependencies`
- `required_capabilities`, `provided_capabilities`, `min_core_version`
- `error_codes`, `message_catalogs`
- `task_handlers`, `schedules`, `event_handlers`
- `lifecycle_hooks`
- `admin_models`, `admin_routes`, `dashboard_widgets`
- `auth_session_store`
- `public_api`

## What `check-app` enforces

- module path is importable
- all required files exist
- migration metadata exists and validates
- no tenant-model contract violations
- all tenant-scoped repository classes are safe (`TenantScopedRepository`/`CrossTenantRepository`)
- router created with `create_router`
- route permissions are declared in `AppModule.permissions`
- all routed responses use `Envelope` / `ListEnvelope`

## Request flow in practice

1. Route policy from `create_router` runs request security middleware.
2. Authentication and tenant resolution run (token, optional `X-Tenant-ID`).
3. Permission resolution returns decision.
4. Router handler executes service logic.
5. Handler returns envelope responses.

## Tenant and permission baseline

- Tenant isolation is enforced by `TenantScopedModel` and `TenantScopedRepository`.
- Token tenant comes from JWT claim `tid`.
- Header tenant uses `X-Tenant-ID`.
- If both are present, they must match.
- Route-level permissions are written as `resource:action` and checked by `PermissionSpec`.

## Extension boundary recommendation

Use APP modules for:

- domain models and repositories
- domain routers and permissions
- domain tasks and schedules
- module configuration metadata

Use core for:

- middleware and security policy changes
- permission backend selection
- migration planner and CLI workflow
- shared platform services

Advanced framework customization belongs to `guides/advanced-architecture.md`.
