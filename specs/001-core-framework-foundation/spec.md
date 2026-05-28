# Feature Specification: Core Framework Foundation

**Feature Branch**: `001-core-framework-foundation`

**Created**: 2026-05-28

**Status**: Draft

**Input**: Convert existing architecture documentation into a spec-driven foundation for a mature multi-tenant FastAPI backend framework.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Scaffold A Compliant App (Priority: P1)

As a framework user, I can create a new app that follows the standard structure and is automatically registered by the framework.

**Why this priority**: App modularity is the primary extension mechanism. If apps are not consistent, the framework cannot scale across teams.

**Independent Test**: Generate or create an example app with `schemas.py`, `models.py`, `router.py`, `services.py`, and `module.py`; run app contract checks and verify it is registered.

**Acceptance Scenarios**:

1. **Given** a new app module, **When** the application starts, **Then** its routers, ORM models, permissions, event handlers, task handlers, and schedule definitions are discovered.
2. **Given** an invalid app module, **When** the app registry validates it, **Then** startup fails with a clear app-contract error.

---

### User Story 2 - Enforce Tenant-Safe Data Access (Priority: P1)

As a business app developer, I can implement tenant-scoped models and repositories without manually adding tenant filters in every query.

**Why this priority**: Tenant isolation is the highest-risk capability in a multi-tenant foundation.

**Independent Test**: Create records in two tenants and verify default repository methods never return cross-tenant data.

**Acceptance Scenarios**:

1. **Given** records in tenant A and tenant B, **When** tenant A lists resources, **Then** only tenant A records are returned.
2. **Given** a cross-tenant query, **When** it does not use the explicit cross-tenant API, **Then** the framework blocks the operation.
3. **Given** a raw SQL operation, **When** it lacks tenant scope metadata, **Then** lint or runtime checks reject it.

---

### User Story 3 - Publish Reliable Side Effects (Priority: P1)

As a service developer, I can update business state and record required events atomically so side effects are not lost on crashes.

**Why this priority**: Audit, permission projections, task dispatch, file cleanup, and notifications cannot rely on best-effort in-process events.

**Independent Test**: Execute a transaction that writes business data and an outbox event, simulate dispatcher failure, and verify the event is retried without duplicating side effects.

**Acceptance Scenarios**:

1. **Given** a business write succeeds, **When** the transaction commits, **Then** the outbox event exists.
2. **Given** a business write rolls back, **When** the transaction ends, **Then** no outbox event remains.
3. **Given** event dispatch fails, **When** retry policy runs, **Then** the event is retried and eventually published or moved to dead letter.

---

### User Story 4 - Ship Database Changes Safely (Priority: P1)

As a platform operator, I can review, dry-run, apply, and verify database migrations safely across apps.

**Why this priority**: Database migrations are a common production failure point and must be governed before business modules grow.

**Independent Test**: Register two app migrations with dependencies, generate a migration plan, run preflight checks, and verify the plan blocks destructive migration without approval.

**Acceptance Scenarios**:

1. **Given** app migration metadata, **When** migration planning runs, **Then** dependencies are topologically sorted.
2. **Given** schema drift, **When** drift check runs, **Then** migration is blocked.
3. **Given** a destructive operation, **When** preflight runs, **Then** it requires explicit classification and approval.

---

### User Story 5 - Authorize With Stable Permission Facts (Priority: P2)

As a platform admin, I can manage role grants and permission templates as facts while the authorization engine consumes projected policies.

**Why this priority**: Authorization must support tenant roles, platform roles, resource scopes, and policy cache invalidation.

**Independent Test**: Grant a role to a user, process the policy projection, and verify authorization result changes with policy versioning.

**Acceptance Scenarios**:

1. **Given** a role grant is created, **When** policy projection runs, **Then** the authorization engine receives the expected policy.
2. **Given** a role is revoked, **When** policy version changes, **Then** permission cache is invalidated.

---

### User Story 6 - Operate Tenant Lifecycle Safely (Priority: P2)

As a platform operator, I can provision, suspend, delete, archive, and audit tenants through a defined lifecycle.

**Why this priority**: Tenant lifecycle controls protect data integrity, compliance, and operational safety.

**Independent Test**: Move a tenant through active, suspended, and deleting states and verify login, read, write, task, and file operations follow the state behavior matrix.

**Acceptance Scenarios**:

1. **Given** a suspended tenant, **When** a write request is made, **Then** it is rejected with a stable code.
2. **Given** a deleting tenant, **When** background cleanup runs, **Then** cleanup steps are idempotent and audited.

---

### Edge Cases

- A user belongs to multiple tenants and sends an invalid `X-Tenant-Id`.
- A platform admin performs cross-tenant access without a reason.
- An outbox dispatcher crashes after performing a side effect but before marking the event as published.
- A migration adds a unique index but duplicate data already exists.
- A soft-deleted record conflicts with a new record's business key.
- A compatibility client requires HTTP 200 for application errors.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST load apps only through declared `AppModule` metadata.
- **FR-002**: System MUST reject invalid app modules at startup or via CLI contract checks.
- **FR-003**: System MUST provide base model, schema, router, service, and repository classes.
- **FR-004**: System MUST enforce tenant-scoped data access through default repositories or query builders.
- **FR-005**: System MUST provide explicit cross-tenant access APIs with permission, reason, and audit requirements.
- **FR-006**: System MUST wrap raw SQL execution and require tenant scope metadata.
- **FR-007**: System MUST resolve tenant context only after validating the authenticated user's membership and tenant lifecycle status.
- **FR-008**: System MUST provide lightweight transactional outbox storage and dispatcher workflow.
- **FR-009**: System MUST support outbox conditional claim, retry, timeout recovery, dead letter, replay, and handler idempotency by event id.
- **FR-010**: System MUST provide migration registration, planning, preflight, dry-run, apply, status, and drift-check commands.
- **FR-011**: System MUST classify migrations as reversible, forward-only, destructive, or backup-restore-required.
- **FR-012**: System MUST support expand-contract migration guidance for breaking changes.
- **FR-013**: System MUST keep tenant membership and role grants as separate facts; role authorization MUST use RoleGrant and authorization-engine policies as projections.
- **FR-014**: System MUST support tenant-level and platform-level permissions as separate scopes.
- **FR-015**: System MUST define tenant lifecycle states and enforce behavior by state.
- **FR-016**: System MUST use a unified JSON response envelope.
- **FR-017**: System MUST use standard HTTP status by default while preserving stable application `code`.
- **FR-018**: System MAY support an explicit compatibility mode for always-200 application errors.
- **FR-019**: System MUST provide deployable process roles for server, worker, scheduler, outbox-dispatcher, and migrate.
- **FR-020**: System MUST provide backup/restore readiness requirements for production profiles.
- **FR-021**: System MUST provide contract tests for tenant isolation, response envelope, app module validity, migrations, outbox, and permissions.

### Key Entities

- **AppModule**: Declares an app's label, version, dependencies, routers, ORM metadata, permissions, events, tasks, and schedules.
- **Tenant**: Represents an isolated organization or workspace.
- **TenantMember**: Represents a user's membership and status in a tenant.
- **PermissionSpec**: Stable permission declaration registered by apps.
- **RoleGrant**: Fact record describing who has which role in which scope.
- **OutboxEvent**: Durable event record written transactionally with business state.
- **MigrationRecord**: Applied migration metadata and status.
- **FileObject**: Tenant-scoped file metadata independent of storage provider.
- **AuditLog**: Append-oriented record of security, admin, file, and business events.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Contract checks fail when an app omits required files or invalid module metadata.
- **SC-002**: Tenant isolation tests prove a tenant cannot read, update, or delete another tenant's records through default repositories.
- **SC-003**: Outbox tests prove committed events survive process crashes and rolled-back events are not published.
- **SC-004**: Migration preflight blocks destructive changes unless classified and approved.
- **SC-005**: Permission projection tests prove facts and policies can be reconciled.
- **SC-006**: All JSON endpoints return the shared envelope and include `request_id`.

## Assumptions

- PostgreSQL is the production database.
- SQLite is only for local development or demo paths where explicitly supported.
- SQLAlchemy 2.x async is the initial ORM and Alembic is the migration baseline; both are wrapped by core abstractions.
- Casbin remains the initial authorization execution engine but not the permission fact source.
- Redis or equivalent storage is available in production for cache, locks, rate limiting, and dispatch coordination.
