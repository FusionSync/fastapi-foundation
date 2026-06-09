# Current Tenant API Contract

## Progress

- Status: `implemented_for_current_scope`
- Done: contract direction agreed: ordinary tenant-domain APIs must use backend request context, not frontend `tenant_id` or `X-Tenant-ID`.
- Done: exceptions are limited to login/tenant selection, tenant switch, tenant-management resources, and platform administrator cross-tenant APIs.
- Done: default tenant resolution rejects `X-Tenant-ID`; ordinary tenant routes now use current request context.
- Done: `platform_access`, `platform_settings`, and tenant-management current-tenant aliases are implemented and covered by tests.
- Next: keep new tenant-domain Platform Apps on current-tenant routes by default; add explicit platform routes for future cross-tenant administration needs.

## Contract

Frontend-controlled tenant selection is only allowed in explicit selection or administration flows.
For ordinary tenant-domain APIs, the tenant comes from the authenticated token/session and the backend request context.

Allowed tenant-bearing flows:

- Login and external-login tenant selection.
- Tenant switch flows that issue a new token/session bound to the selected tenant.
- Tenant-management resources such as `/tenants/{tenant_id}/members` and `/tenants/{tenant_id}/invitations`.
- Platform administrator cross-tenant APIs under `/platform/...`, protected by platform-scope permissions.

Disallowed for ordinary tenant-domain APIs:

- `X-Tenant-ID` as a fallback tenant selector.
- URL path tenant ids that are immediately required to equal the current context tenant.
- Query/body tenant ids for menu, button, or operation-permission checks.

## Route Shape

Ordinary tenant app APIs should use current-tenant routes:

```text
/access/role-grants
/settings/values
/me/permissions
/me/permissions/check
```

Tenant-management APIs can keep tenant path routes and also expose current-tenant aliases for tenant administrators:

```text
/tenants/{tenant_id}/members
/tenants/{tenant_id}/invitations
/tenant/members
/tenant/invitations
```

## TODO

- [x] Add tests proving `X-Tenant-ID` is rejected for ordinary tenant-domain APIs.
- [x] Stop accepting `X-Tenant-ID` in default tenant resolution.
- [x] Remove Header fallback from pure tenant resolver helpers.
- [x] Remove Header fallback from rate-limit tenant dimensions.
- [x] Add current-tenant `platform_access` role-grant routes.
- [x] Add current-tenant `platform_settings` value routes.
- [x] Add `GET /me/permissions`.
- [x] Add `POST /me/permissions/check`.
- [x] Add current-tenant tenant-management aliases for members and invitations.
- [x] Keep platform cross-tenant and tenant-management path routes explicitly documented.
- [x] Update tests and module documentation touched by this contract.
- [ ] Add platform-admin cross-tenant variants later if product operations need them.
