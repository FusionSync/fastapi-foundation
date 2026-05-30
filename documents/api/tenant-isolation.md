# Tenant Isolation Contract

Isolation is enforced by model design + repository layer + router policy.

## Tenant model rules

For tenant-scoped data:

- model inherits `TenantScopedModel`.
- table has `tenant_id` and it is non-null.
- repository for the model should inherit `TenantScopedRepository`.
- raw ORM access in tenant app router/service is discouraged and checked by conformance.

## Tenant resolution flow

`DatabaseTenantContextResolver` resolves tenant with:

- token tenant (`tid`)
- header tenant (`X-Tenant-ID`)
- current user default tenant
- tenant lifecycle policy and state checks

When token tenant and header tenant mismatch:

- raise `TENANT_CONTEXT_CONFLICT`.

## Access modes

### Tenant scoped route

Use default `tenant_required=True` in router and route permission scope `tenant`.

### Platform scoped route

Set `permission_scope="platform"` in `create_router` when route manages system-wide resources.

### No tenant route

Use `tenant_required=False` for safe public or platform identity endpoints.

## Repository behavior

`TenantScopedRepository`:

- reads tenant from request context
- filters all queries by tenant
- rejects writes that modify different tenant id
- inserts implicit tenant id if missing

`CrossTenantRepository`:

- must receive permission gate and optional reason
- designed for explicit cross-tenant admin scenarios

## Cross-tenant error handling

Use `reason` + platform decision when using cross-tenant permissions:

- missing target tenants -> permission denied
- platform decision missing -> permission denied
- decision tenant mismatch -> permission denied

This is strict by design to keep auditability high.
