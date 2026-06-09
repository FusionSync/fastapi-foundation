# Platform Settings

## Progress

- Status: `partial`
- Done: standard `platform_settings` app module is registered with permissions, routes, models metadata, migrations metadata, setting definitions and public API.
- Done: `SettingSpec` declarations are collected by `SettingRegistry` during app startup.
- Done: platform and tenant setting value APIs persist DB overrides and write `SettingRevision` rows.
- Done: resolver uses `tenant override -> platform override -> registered default`.
- Done: sensitive and secret-ref settings have redacted response serialization.
- Done: `files.max_file_size_mb` and `auth.password_min_length` are consumed by files/accounts runtime APIs when `platform_settings` is installed.
- Done: platform/tenant setting list/read, reset, history, audit, cache invalidation rule and `expected_version` optimistic concurrency are implemented.
- Done: validate/dry-run API checks registered setting definitions without writing `SettingValue` rows.
- Done: built-in setting operator notes document module/category/scope and runtime impact.
- Next: add UI/operator workflows for grouped setting modules and secret-reference provider integration.

## Design Notes

`platform_settings` is not a generic KV store. Definitions live in code through `SettingSpec`; only scoped overrides live in the database.

The design borrows three proven ideas:

- Django and Spring Boot style typed configuration: definitions have ownership, type, default and validation.
- Kubernetes ConfigMap/Secret split: normal values and secret references are handled differently.
- OpenFeature-style evaluation precedence: request or tenant context can affect resolution without changing the definition contract.

## Built-in Settings

| Setting | Scope | Category | Runtime impact |
| --- | --- | --- | --- |
| `files.max_file_size_mb` | platform, tenant | `file_policy` | Controls max upload size when `platform_files` resolves upload policy. |
| `auth.password_min_length` | platform | `security_policy` | Controls local password creation/reset minimum length when accounts consumes settings. |
| `tenancy.allow_self_service_tenant_create` | platform | `tenant_policy` | Reserved platform policy switch for self-service tenant creation. |

Operators can dry-run value changes before writing an override:

```bash
POST /api/v1/platform/settings/validate/files/max_file_size_mb
{
  "scope": "platform",
  "value": 256
}
```

The response returns the normalized value, `scope_id`, `value_type`, `valid=true` and `dry_run=true`.

## TODO

- [x] Register typed built-in settings.
- [x] Add `SettingValue` and `SettingRevision` models.
- [x] Add platform setting value API.
- [x] Add tenant setting value API.
- [x] Add resolver API with provenance.
- [x] Cover tenant override precedence with integration tests.
- [x] Apply `files.max_file_size_mb` to file upload validation.
- [x] Apply `auth.password_min_length` to local password creation/reset validation.
- [x] Add platform and tenant setting value list/read APIs.
- [x] Add setting reset/delete APIs.
- [x] Add setting revision/history APIs.
- [x] Add `expected_version` optimistic concurrency.
- [x] Add validate/dry-run API.
- [x] Add audit events for create/update/reset.
- [x] Add cache invalidation after value changes.
- [x] Add per-setting documentation for built-in keys.
- [x] Redact sensitive values and forbid plaintext storage for secret-only settings.
- [x] Add runtime consumer and concurrency checkpoint tests.
- [x] Add unknown-key, invalid-scope and invalid-value checkpoint tests for validate/dry-run.
- [ ] Add runtime-mutable, sensitive-value, secret-ref-only and cache invalidation checkpoint tests.
