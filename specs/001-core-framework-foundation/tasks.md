# Tasks: Core Framework Foundation

**Input**: Design documents from `specs/001-core-framework-foundation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Contract and integration tests are required because this is a framework foundation.

**Testing Cadence**: Do not run a broad suite after every small task. Complete one large capability, then run its checkpoint suite. Small local smoke checks are allowed only when they unblock the current capability.

**Organization**: Tasks are grouped by user story and foundational phase.

## Phase 1: Setup

**Purpose**: Establish project skeleton and development tooling.

- [x] T001 Create `pyproject.toml` with FastAPI, Pydantic, SQLAlchemy 2.x async, Alembic, pytest, lint, and type-check dependencies
- [x] T002 Create source tree under `src/core`, `src/platform_apps`, `src/apps`, and `server`
- [x] T003 [P] Create test tree under `tests/contract`, `tests/integration`, and `tests/unit`
- [x] T004 [P] Add `.env.example` for local profile
- [x] T005 [P] Configure formatting, linting, and import sorting

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core primitives that block all user stories.

**CRITICAL**: No app implementation should begin until this phase is complete.

- [x] T006 Implement settings loading in `src/core/config/settings.py`
- [x] T007 Implement request context in `src/core/context`
- [x] T008 Implement response envelope helpers in `src/core/serialization/responses.py`
- [x] T009 Implement core exception types and handlers in `src/core/exceptions`
- [x] T010 Implement app module contract in `src/core/apps/module.py`
- [x] T011 Implement app registry and validation in `src/core/apps/registry.py`
- [x] T012 Implement FastAPI app factory in `src/core/app/factory.py`
- [x] T013 Implement base schemas, models, routers, and services in `src/core/base`
- [x] T014 Implement error code registry and `code -> HTTP status -> headers` mapping
- [x] T015 Implement security startup checks for production secrets, CORS, trusted hosts, and auth provider
- [x] T016 Implement observability health endpoints and metrics contract
- [x] T017 [Checkpoint] Run foundational contract tests for response envelope, app module validation, config check, and health endpoints

**Checkpoint**: Core app can start with no business apps and returns health responses.

---

## Phase 3: User Story 1 - Scaffold A Compliant App (Priority: P1)

**Goal**: App modules can be registered and validated consistently.

**Independent Test**: Example app passes app contract checks.

### Tests

- [x] T018 Add example app contract test in `tests/contract/test_example_app_contract.py`
- [x] T019 Add CLI contract check test in `tests/contract/test_cli_check_app.py`

### Implementation

- [x] T020 Create `src/apps/example_domain/module.py`
- [x] T021 Create standard app files: `schemas.py`, `models.py`, `router.py`, `services.py`, `permissions.py`
- [x] T022 Implement `core check-app` CLI command
- [x] T023 Implement `core list-apps --json` CLI command
- [x] T024 Implement import/dependency lint for app `public_api` boundaries
- [x] T025 [Checkpoint] Run app contract and golden app tests

**Checkpoint**: Example app registers through the framework without manual imports.

---

## Phase 4: User Story 2 - Enforce Tenant-Safe Data Access (Priority: P1)

**Goal**: Default repository behavior prevents cross-tenant access.

**Independent Test**: Tenant A cannot access tenant B data through default repositories.

### Tests

- [x] T026 Add tenant resolver membership/status tests in `tests/contract/test_tenant_resolver.py`
- [x] T027 Add tenant isolation tests in `tests/contract/test_tenant_isolation.py`
- [x] T028 Add raw SQL guard and ORM bypass lint tests in `tests/contract/test_raw_sql_guards.py`

### Implementation

- [x] T029 Implement `resolve_current_tenant()` with token/header/default precedence and membership/status gates
- [x] T030 Implement `TenantScopedModel` in `src/core/base/models.py`
- [x] T031 Implement `TenantScopedRepository` and `TenantScopedQuery` in `src/core/base/repositories.py`
- [x] T032 Implement `CrossTenantRepository` with permission, reason, and audit requirements
- [x] T033 Implement raw SQL wrappers in `src/core/db/sql.py`
- [x] T034 Add tenant-scoped database constraint checks to migration helpers
- [x] T035 [Checkpoint] Run tenant resolver, tenant isolation, and raw SQL guard tests

**Checkpoint**: Tenant isolation tests pass against PostgreSQL.

---

## Phase 5: User Story 3 - Publish Reliable Side Effects (Priority: P1)

**Goal**: Business writes and reliable events are transactionally linked.

**Independent Test**: Committed writes leave outbox events; rolled-back writes do not.

### Tests

- [x] T036 Add outbox transaction tests in `tests/integration/test_outbox_transaction.py`
- [x] T037 Add outbox dispatcher retry/dead-letter tests in `tests/integration/test_outbox_dispatcher.py`

### Implementation

- [x] T038 Implement lightweight `OutboxEvent` model in `src/core/outbox/models.py`
- [x] T039 Implement outbox repository in `src/core/outbox/repository.py`
- [x] T040 Implement unit-of-work helper that shares DB connection between business writes and outbox writes
- [x] T041 Implement dispatcher conditional claim, retry, timeout recovery, and dead-letter handling
- [x] T042 Implement event registry and `event_id` idempotent handler contract
- [x] T043 [Checkpoint] Run outbox transaction and dispatcher tests

**Checkpoint**: Outbox survives dispatcher failures and supports replay.

---

## Phase 6: User Story 4 - Ship Database Changes Safely (Priority: P1)

**Goal**: Migrations are planned, checked, and applied safely.

**Independent Test**: Migration plan detects dependencies, drift, and destructive operations.

### Tests

- [x] T044 Add migration registry tests in `tests/contract/test_migration_registry.py`
- [x] T045 Add migration preflight tests in `tests/integration/test_migration_preflight.py`

### Implementation

- [x] T046 Implement migration metadata collection in `src/core/migrations/registry.py`
- [x] T047 Implement migration manifest fields for phase, destructive flag, approval, backfill plan, and lock risk
- [x] T048 Implement migration dependency planner in `src/core/migrations/planner.py`
- [x] T049 Implement preflight checks in `src/core/migrations/preflight.py`
- [x] T050 Implement drift check command in `src/core/migrations/drift.py`
- [x] T051 Implement CLI commands: `migrate plan`, `preflight`, `dry-run`, `apply`, `status`, `drift-check`
- [x] T052 [Checkpoint] Run migration registry, preflight, and drift tests

**Checkpoint**: Destructive migrations are blocked without explicit classification.

---

## Phase 7: User Story 5 - Authorize With Stable Permission Facts (Priority: P2)

**Goal**: Role grants are facts; authorization policies are projections.

**Independent Test**: Role grant changes update projected policies and invalidate cache.

- [x] T053 Add permission projection tests in `tests/integration/test_permission_projection.py`
- [x] T054 Implement `PermissionSpec`, `RoleTemplate`, and `RoleGrant` models
- [x] T055 Ensure `TenantMember` stores membership/status only, not role grants
- [x] T056 Implement permission registry collection from app modules
- [x] T057 Implement Casbin policy projector through outbox event
- [x] T058 Implement permission reconciliation command
- [x] T059 [Checkpoint] Run permission projection and reconciliation tests

---

## Phase 8: User Story 6 - Operate Tenant Lifecycle Safely (Priority: P2)

**Goal**: Tenant state controls login, reads, writes, tasks, and files.

**Independent Test**: Suspended and deleting tenants enforce behavior matrix.

- [x] T060 Add tenant lifecycle behavior tests in `tests/integration/test_tenant_lifecycle.py`
- [x] T061 Implement tenant lifecycle state machine
- [x] T062 Implement tenant provisioning workflow
- [x] T063 Implement tenant suspension behavior gates
- [x] T064 Implement tenant deletion outbox workflow
- [x] T065 Implement token/session revocation hook for tenant suspend/delete
- [x] T066 [Checkpoint] Run tenant lifecycle matrix tests

---

## Phase 9: Operations Foundation (Priority: P1)

**Goal**: The framework has a deployable process model and restore story before real business apps depend on it.

- [x] T067 Implement CLI process commands: `serve`, `worker`, `scheduler`, `outbox-dispatcher`
- [x] T068 Implement `core check-config --profile <profile> --json`
- [x] T069 Add deployment smoke command for local/private profiles
- [x] T070 Add backup-readiness check hook for migration preflight
- [x] T071 Add health checks for worker, scheduler, and outbox-dispatcher
- [x] T072 [Checkpoint] Run operations smoke checks for local profile

---

## Phase N: Polish & Cross-Cutting Concerns

- [x] T073 Add OpenTelemetry-ready metrics names for HTTP, outbox, migrations, and tenant isolation failures
- [x] T074 Add documentation updates under `docs/modules` and `docs/operations`
- [x] T075 Add golden app quickstart validation
- [x] T076 Run full contract, integration, and unit test suite

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup has no dependencies.
- Foundational blocks all user stories.
- US1 can begin after Foundational.
- US2 depends on base models, context, and example app.
- US3 depends on transaction helpers.
- US4 depends on app registry and database setup.
- US5 depends on outbox and permissions registry.
- US6 depends on tenancy, tasks, outbox, and audit.
- Operations foundation depends on config, observability, tasks, scheduler, outbox, and migrations.

### Parallel Opportunities

- T003-T005 can run in parallel.
- Foundational error-code, security, and observability work can be split by file ownership.
- US2 tenant resolver, repository guard, and raw SQL wrapper can be split by file ownership, then tested at checkpoint.
- US3 outbox model/repository and dispatcher can be implemented in parallel, then tested at checkpoint.
- US4 planner, manifest/preflight, and drift-check can be split by file ownership, then tested at checkpoint.

## Implementation Strategy

1. Build the minimal core runtime first.
2. Complete one large capability at a time, then run that capability's checkpoint tests.
3. Prove app registration with the example app.
4. Implement tenant isolation before real platform apps grow.
5. Implement lightweight outbox before reliable side effects.
6. Implement migration governance before adding many models.
7. Add permission facts and tenant lifecycle after foundations are testable.
8. Add operations foundation before cloud/private deployment claims.
