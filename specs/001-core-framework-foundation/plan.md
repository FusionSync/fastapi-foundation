# Implementation Plan: Core Framework Foundation

**Branch**: `001-core-framework-foundation` | **Date**: 2026-05-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-core-framework-foundation/spec.md`

## Summary

Build a reusable FastAPI backend foundation with app registration, request context, base classes, tenant isolation, permission facts and policy projection, transactional outbox, migration governance, tenant lifecycle, unified API envelope, and contract testing.

The plan intentionally treats the framework as a product. The first implementation milestone should produce a working golden app that proves the framework constraints are enforced.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.x async, Alembic, PostgreSQL, Redis, Casbin, pytest

**Storage**: PostgreSQL for application state; Redis for cache/locks/rate limit/coordination; local filesystem or S3-compatible storage through provider abstraction

**Testing**: pytest, HTTPX test client, PostgreSQL-backed integration tests, contract tests

**Target Platform**: Linux server, local Windows/macOS development supported

**Project Type**: Backend framework / web-service foundation

**Performance Goals**: Framework overhead should remain small enough for normal API workloads; tenant filters, context setup, and response envelope should not dominate request latency

**Constraints**:

- Core cannot import platform or business apps.
- Apps must integrate through `module.py`.
- Tenant isolation must be enforced by framework mechanisms.
- Production cannot auto-generate schemas at runtime.
- Reliable side effects must use transactional outbox.
- JSON APIs must use response envelope.
- Testing cadence follows capability checkpoints: finish a large capability, then run its targeted checkpoint suite.

**Scale/Scope**:

- Multiple tenants in shared PostgreSQL schema.
- Multiple apps loaded by registry.
- Multiple deployment modes: local, private, cloud.
- Production process roles: server, worker, scheduler, outbox-dispatcher, migrate.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Core Boundary**: PASS. All planned app integrations go through module metadata.
- **Tenant Isolation By Construction**: PASS with required tenant resolver, tenant repository/query, raw SQL wrapper, and bypass lint tasks.
- **Contract-First APIs**: PASS with response envelope and API status strategy.
- **Reliable State Changes**: PASS with lightweight transactional outbox as P1 foundation work.
- **Migration Safety**: PASS with migration registry, preflight, dry-run, and expand-contract rules.
- **Security And Observability**: PASS with security startup checks, context hardening, audit, health, and metrics planned as framework defaults.
- **App Contracts Must Be Enforced**: PASS with scaffold, lint, CLI checks, and contract tests planned.

## Project Structure

### Documentation (this feature)

```text
specs/001-core-framework-foundation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── api-envelope.openapi.yaml
└── tasks.md
```

### Source Code (repository root)

```text
src/
├── core/
│   ├── app/
│   ├── apps/
│   ├── base/
│   ├── config/
│   ├── context/
│   ├── db/
│   ├── migrations/
│   ├── auth/
│   ├── security/
│   ├── tenancy/
│   ├── permissions/
│   ├── cache/
│   ├── locks/
│   ├── idempotency/
│   ├── rate_limit/
│   ├── quotas/
│   ├── storage/
│   ├── http_clients/
│   ├── tasks/
│   ├── scheduler/
│   ├── events/
│   ├── outbox/
│   ├── exceptions/
│   ├── serialization/
│   ├── messages/
│   ├── admin/
│   ├── cli/
│   ├── testing/
│   └── observability/
├── platform_apps/
│   ├── accounts/
│   ├── tenants/
│   ├── files/
│   ├── audit/
│   └── admin/
└── apps/
    └── example_domain/

server/
└── main.py

tests/
├── contract/
├── integration/
└── unit/

docs/
├── architecture/
├── modules/
├── operations/
└── decisions/
```

**Structure Decision**: Use a single backend project with `src/core`, `src/platform_apps`, and `src/apps`. The framework is not split into separate Python packages until code boundaries prove stable.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Lightweight transactional outbox in foundation | Reliable side effects are required for audit, permission projection, and tasks | In-process events can lose side effects on crash |
| Tenant-scoped repository layer | Multi-tenant isolation must be enforced | Manual tenant filters are too easy to forget |
| Migration governance layer | Production database changes need preflight and drift checks | Running raw migration commands does not provide release safety |
| Permission facts plus policy projection | Casbin policies should not be the source of truth | Direct Casbin mutation makes reconciliation and audit harder |
