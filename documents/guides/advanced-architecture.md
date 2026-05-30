# Advanced architecture and extension points

This section is for developers who need to extend framework behaviour instead of only writing APP modules.

## 1) Extension boundary

### Safe extension (APP layer)

- models, schemas, services, routers
- business permissions
- task handlers and events
- migrations

### Core extension (framework layer)

- request security pipeline
- permission backends and cache strategies
- task/event/outbox internals
- migration planner/execution behaviour
- system health/readiness diagnostics

If you can implement your need with APP layer, do that first.

## 2) AppModule as integration contract

`core.apps.AppModule` is the integration boundary. Key fields:

- `label`: unique app id, used in dependency graph and diagnostics
- `version`: for versioned release governance
- `dependencies`: strict order and startup ordering
- `migrations`: runtime migration path
- `permissions`: route and policy declaration
- `min_core_version`: version gate against runtime
- `required_capabilities` / `provided_capabilities`
- `message_catalogs`, `error_codes`, `task_handlers`, `schedules`, `lifecycle_hooks`

## 3) Capability model

Runtime capabilities can be extended by process settings and optional providers.

`resolve_runtime_capabilities(settings)` returns active runtime flags used by `AppRegistry`.

- If required capability is missing, registry load blocks.
- This is the standard mechanism for optional external dependencies.

## 4) Registry and diagnostics for release safety

- `core list-apps` and `core check-app` should be mandatory in CI.
- CI should block merge on:
  - missing dependency
  - route permission declaration mismatch
  - tenant query lint errors
- `core permissions catalog` and `core permissions reconcile` should be part of release checks when role/permission changed.

## 5) Migration and release flow design

Migration phases:

- `expand`
- `backfill`
- `contract`
- `maintenance`

Recommended order:

1. `core migrate plan`
2. `core migrate preflight`
3. `core migrate apply --yes` in maintenance window
4. `core serve --run --dry-run`
5. `core smoke`

Use `--phase` for staged rollout where needed.

## 6) How to add a new security hook

1. Add a context hook in service init path.
2. Add handler in APPModule `lifecycle_hooks`.
3. Keep handler signature one argument (context object).
4. Ensure idempotent, fast startup/shutdown hooks.

## 7) Anti-patterns

- modifying core for business rules that belong to APP
- bypassing route security with extra endpoints
- injecting raw SQL/ORM statements directly in tenant-scoped routers
- broad scopes in permissions without explicit risk documentation

If you need core-level change, add a short architecture note + migration note before coding.
