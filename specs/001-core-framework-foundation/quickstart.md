# Quickstart: Core Framework Foundation

This quickstart describes the expected developer flow after the foundation exists.

## 1. Check Configuration

```bash
core check-config --profile local --json
```

Expected:

- Environment profile is valid.
- Required secrets are present.
- Production defaults are safe.

## 2. List Registered Apps

```bash
core list-apps --installed-app apps.example_domain.module --json
```

Expected:

- `apps.example_domain`

## 3. Validate App Contracts

```bash
core check-app apps.example_domain.module --json
```

Expected:

- Required files exist.
- `module.py` metadata is valid.
- Permission specs are registered.
- ORM metadata is registered.

## 4. Plan Migrations

```bash
core migrate plan --installed-app apps.example_domain.module --json
core migrate preflight --installed-app apps.example_domain.module --json
core migrate dry-run --installed-app apps.example_domain.module --json
```

Expected:

- Migration dependency order is valid.
- No schema drift exists.
- Destructive operations are flagged.

## 5. Run Capability Checkpoint Tests

```bash
uv run pytest
```

Expected:

- Tenant isolation tests pass.
- Response envelope tests pass.
- Outbox tests pass.
- Migration checks pass.

Testing cadence:

- Complete one large capability before running its checkpoint suite.
- Do not run the full suite after every small task unless a failure blocks the current capability.

## 6. Start API Server

```bash
uvicorn server.main:app --reload
```

Expected:

- `/healthz` returns alive status.
- `/readyz` checks database/cache/storage.
- API routes are registered under `/api/v1`.

## 7. Validate Example App

Use the example app to verify:

- Create resource in tenant A.
- Create resource in tenant B.
- Tenant A cannot read tenant B resource.
- Unauthorized request returns stable `code` and appropriate HTTP status.
- Business write records outbox event.
