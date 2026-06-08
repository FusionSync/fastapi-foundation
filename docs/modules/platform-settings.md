# Platform Settings

## Progress

- Status: `partial`
- Done: standard `platform_settings` app module is registered with permissions, routes, models metadata, migrations metadata, setting definitions and public API.
- Done: `SettingSpec` declarations are collected by `SettingRegistry` during app startup.
- Done: platform and tenant setting value APIs persist DB overrides and write `SettingRevision` rows.
- Done: resolver uses `tenant override -> platform override -> registered default`.
- Done: sensitive and secret-ref settings have redacted response serialization.
- Next: audit records, cache invalidation, reset/delete APIs, full built-in setting docs and stricter tests for unknown keys and sensitive values.

## Design Notes

`platform_settings` is not a generic KV store. Definitions live in code through `SettingSpec`; only scoped overrides live in the database.

The design borrows three proven ideas:

- Django and Spring Boot style typed configuration: definitions have ownership, type, default and validation.
- Kubernetes ConfigMap/Secret split: normal values and secret references are handled differently.
- OpenFeature-style evaluation precedence: request or tenant context can affect resolution without changing the definition contract.

## TODO

- [x] Register typed built-in settings.
- [x] Add `SettingValue` and `SettingRevision` models.
- [x] Add platform setting value API.
- [x] Add tenant setting value API.
- [x] Add resolver API with provenance.
- [x] Cover tenant override precedence with integration tests.
- [ ] Add setting reset/delete APIs.
- [ ] Add audit events for create/update/reset.
- [ ] Add cache invalidation after value changes.
- [ ] Add per-setting documentation for built-in keys.
- [ ] Add sensitive-value and unknown-key checkpoint tests.
