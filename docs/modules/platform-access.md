# Platform Access

## Progress

- Status: `partial`
- Done: standard `platform_access` app module is registered with permissions, routes, models metadata, migrations metadata and public API.
- Done: permission catalog API reads the runtime `PermissionRegistry`.
- Done: platform administrator grant API writes `RoleGrant` rows in the `__platform__` domain instead of adding a user flag.
- Done: route authorization uses `access.permission:read` and `access.platform_admin:manage` with platform scope.
- Next: role template CRUD, tenant role grant CRUD, effective-permission explain API, projection reconcile API, audit records and cache invalidation tests.

## Design Notes

`platform_access` is the SaaS IAM control plane. It is intentionally built on top of core permission facts:

```text
PermissionSpec -> RoleTemplate -> RoleGrant -> ProjectedPolicy -> AuthorizationDecision
```

This follows the same split used by mature authorization systems: Django keeps permissions separate from users, Casbin models tenant/domain-aware RBAC, and Zanzibar-style systems treat authorization facts as durable inputs to evaluated decisions. The foundation does not add `is_platform_admin` to `users`.

## TODO

- [x] Register `platform_access` as a standard AppModule.
- [x] Add permission catalog API.
- [x] Add platform administrator grant API.
- [x] Cover platform admin grants with integration tests.
- [ ] Add role template list/create/update APIs.
- [ ] Add tenant role grant/revoke APIs.
- [ ] Add effective permission explanation APIs.
- [ ] Add projection reconcile API.
- [ ] Add audit events for high-risk IAM mutations.
