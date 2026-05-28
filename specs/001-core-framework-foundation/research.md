# Research: Core Framework Foundation

## Decision: Use Spec-Driven Structure For Framework Work

**Decision**: Keep existing `docs/` module documentation, but add spec-kit style feature packages under `specs/`.

**Rationale**: The current module docs are good references, but spec-kit style artifacts provide implementation traceability: spec -> plan -> data model -> contracts -> tasks.

**Alternatives considered**:

- Replace `docs/` entirely: rejected because module docs are useful long-term references.
- Keep only docs: rejected because implementation tasks and acceptance checks are not explicit enough.

## Decision: Standard HTTP Status Plus Stable Code

**Decision**: Use shared JSON envelope and standard HTTP status by default.

**Rationale**: Security tooling, API gateways, clients, APM, and SLO reporting expect real HTTP semantics. Stable application `code` still gives SDKs and business logic a durable contract.

**Alternatives considered**:

- Always HTTP 200: rejected as default because it weakens monitoring, caching, retry, WAF, and client behavior. Keep only as explicit compatibility mode.

## Decision: Tenant Isolation Through Repository And SQL Guards

**Decision**: Introduce `TenantScopedRepository`, `TenantScopedQuery`, and raw SQL wrappers.

**Rationale**: Multi-tenant isolation cannot rely on developer discipline. Framework-owned access paths reduce accidental leakage and make contract testing possible.

**Alternatives considered**:

- Manual filters in services: rejected as unsafe.
- PostgreSQL RLS immediately: useful later, but adds operational complexity. Repository guards are still needed for portability and testing.

## Decision: Transactional Outbox For Reliable Events

**Decision**: Events that drive critical side effects must be written to `outbox_events` in the same transaction as business state. The first version is intentionally lightweight: table-backed events, simple status machine, conditional claim, bounded retry, dead letter, replay, and handler idempotency by `event_id`.

**Rationale**: This prevents lost events and ghost events during crashes or transaction rollbacks.

**Alternatives considered**:

- In-process event bus: useful only for non-critical, best-effort side effects.
- Publish to message queue inside the transaction: cannot share the database transaction and can still produce inconsistencies.
- Event sourcing platform: rejected for the foundation because the current need is reliable side effects, not rebuilding aggregate state from events.

## Decision: Migration Governance Before Feature Growth

**Decision**: Build migration registration, planning, preflight, drift checks, and expand-contract guidance early.

**Rationale**: The framework will host multiple apps. Without migration governance, app growth creates release risk and operational coupling.

**Alternatives considered**:

- Let each app run migrations independently: rejected because cross-app dependencies and production safety need a single plan.

## Decision: SQLAlchemy 2.x Async With Alembic

**Decision**: Use SQLAlchemy 2.x async for ORM/data access and Alembic for migrations, wrapped behind core base models, repositories, unit-of-work, and raw SQL utilities.

**Rationale**: The foundation needs strong transaction/session control, mature migration tooling, PostgreSQL-specific DDL support, and a long-lived ecosystem. SQLAlchemy/Alembic fits the multi-tenant foundation better than a lighter CRUD-first ORM.

**Alternatives considered**:

- Tortoise/Aerich: simpler async CRUD ergonomics, rejected as the foundation baseline because transaction propagation, mature migration governance, and complex PostgreSQL operations would require more custom framework code.

## Decision: Permission Facts Plus Policy Projection

**Decision**: Treat tenant membership and role grants as separate source-of-truth facts; treat Casbin policies as a projection.

**Rationale**: Facts are auditable and reconcilable. Policy engines are optimized for authorization decisions, not lifecycle governance.

**Alternatives considered**:

- Store all authorization state directly in Casbin: rejected because audit, versioning, cache invalidation, and reconciliation become harder.
