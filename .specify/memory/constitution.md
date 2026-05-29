# FastAPI Foundation Constitution

## Core Principles

### I. Core Boundary Is Non-Negotiable

`core` provides framework capabilities only: app runtime, configuration, context, base classes, database, authentication abstraction, authorization abstraction, tenancy, storage, tasks, scheduling, events, outbox, observability, CLI, and testing infrastructure.

`core` MUST NOT import `platform_apps` or `apps`. Platform and business capabilities integrate through declared app modules only.

### II. Tenant Isolation By Construction

Tenant isolation must be enforced by framework mechanisms, not developer memory.

Every tenant-scoped model MUST include `tenant_id`. Tenant-scoped repositories MUST inject tenant filters by default. Cross-tenant access MUST use explicit APIs, require platform permission, require a reason, and produce audit records.

### III. Contract-First APIs

All JSON APIs MUST use the shared response envelope. HTTP status expresses protocol, authentication, authorization, rate limit, and system semantics. The response `code` expresses stable application semantics.

Routers MUST NOT return raw dictionaries, raw lists, or ORM models. Responses MUST pass through core response helpers.

### IV. Reliable State Changes

Business writes that need downstream side effects MUST use transactional boundaries and transactional outbox.

Events that drive audit projections, permission projections, tasks, notifications, file cleanup, or external integrations MUST be recorded in the same transaction as the business state change.

### V. Migration Safety

Production database changes MUST go through migration planning, preflight checks, drift checks, and review. Destructive changes MUST use expand-contract rollout unless explicitly approved as one-off maintenance.

Runtime schema generation is forbidden in production.

### VI. Security And Observability Are Framework Defaults

Security controls, request context, structured logging, audit trails, metrics, trace IDs, health checks, process roles, and backup readiness are framework-level concerns. Apps may add domain-specific details, but cannot bypass core security, audit, observability, or operations hooks.

### VII. App Contracts Must Be Enforced

Every app MUST expose a stable `module.py` with label, version, app dependencies, routers, ORM metadata, permissions, event handlers, task handlers, and schedule definitions.

The framework MUST provide scaffold, lint, CLI checks, and contract tests to enforce app structure and behavior.

## Additional Constraints

- Primary runtime target: Python 3.11+.
- Primary web framework: FastAPI.
- Primary production database: PostgreSQL.
- Local and demo deployments may use SQLite only where explicitly supported.
- ORM choice must be wrapped by core base classes and repositories.
- External provider choices must remain replaceable behind core abstractions.

## Development Workflow

- Specification comes before implementation.
- Plans must include tenant isolation, permission, migration, testing, observability, deployment, backup/restore, and rollback considerations.
- Tasks must be ordered so foundational framework constraints are implemented before business app features.
- Verification should run at capability checkpoints, not after every small implementation task, unless a local check is needed to unblock progress.
- Contract tests are required for every app integration.

## Governance

This constitution supersedes ad hoc project conventions. Any exception must be recorded in a plan's Complexity Tracking section with a reason, rejected simpler alternative, risk, and mitigation.

All architecture changes must update the relevant spec, plan, and module documentation.

**Version**: 0.1.0 | **Ratified**: 2026-05-28 | **Last Amended**: 2026-05-28
