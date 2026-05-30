# API Environment Contract

`core check-config` validates runtime configuration; `core config template/drift-check` standardize profiles.

## Settings schema (high level)

- `app`: name/version/env/debug
- `api`: prefix, error status mode
- `security`: jwt secret, cors, trusted hosts, max request size
- `database`: url/read_url/pool settings/tenant fallback
- `observability`: service role and metrics
- `task_queue`: provider, attempts, backoff
- `scheduler`: provider and lock settings
- `tenant_lifecycle`: tenant state controls
- `dependencies`: redis/object storage/oidc URLs
- `installed_apps`: list of module paths

Env parsing uses nested delimiter with `__` (example: `APP__ENV`).

## Profiles and recommended values

- `local`: `core config template --profile local --json`
- `private`: `core config template --profile private --json`
- `cloud`: `core config template --profile cloud --json`

`core config drift-check --profile <profile> --json` compares actual env to profile and returns:

- `has_drift`: boolean
- `checked`: key list
- `missing`: missing required entries
- `mismatched`: values that do not match expectation

## Environment variable quick map

Examples used by templates:

- `APP__ENV` (local/private/cloud)
- `DATABASE__URL`
- `API__ERROR_HTTP_STATUS_MODE`: usually `standard`
- `SECURITY__JWT_SECRET` (local dev) or `SECURITY__JWT_SECRET_REF` (private/cloud)
- `SECURITY__TRUSTED_HOSTS`
- `SECURITY__CORS_ORIGINS`
- `TASK_QUEUE__PROVIDER` (`sync` | `database`)
- `SCHEDULER__PROVIDER` (`local` | `apscheduler` | `celery_beat`)
- `OBSERVABILITY__SERVICE_ROLE`
- `INSTALLED_APPS` (JSON array string)

## Response format

Most JSON mode CLI responses use:

- `ok`: bool
- `command`: command path (string)
- `error`: error object for failure modes
- command-specific payload fields (drift/app checks/results)

Use `--json` for all operations used in scripts and CI.
