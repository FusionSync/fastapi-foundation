# App Registry Contract

APP loading follows a strict pipeline:

1. read `installed_apps`
2. import each module path
3. validate conformance
4. validate dependency graph and core compatibility
5. register error codes and message catalogs
6. build runtime registries (permissions, tasks, migrations, events, schedule)

## AppModule contract

Core required fields typically used:

- `label` (unique module label)
- `version`
- `routers`
- `models` (module import paths)
- `permissions`
- `migrations` (path to manifest)

Useful optional fields:

- `dependencies`
- `required_capabilities`
- `provided_capabilities`
- `min_core_version`

`core.apps.conformance.check_app` enforces:

- required files
- permission declaration completeness
- route security policy alignment
- tenant model constraints
- repository inheritance constraints
- response envelope usage
- duplicate/missing permission codes

## CLI inspection

- `core list-apps --json`
- `core check-app <module>.module --json`
- `core list-apps --installed-app <label> --json`

## CLI output shape (expected)

`list-apps` payload includes:

- `ok`
- `apps`: per module metadata (`label`, `version`, `dependencies`, `permissions`, counts)
- `diagnostics`: aggregate and load order

`check-app` payload includes:

- `module_path`
- `label`
- `ok`
- `errors`
- `warnings`

## Runtime visibility

At runtime:

- `registry.routers` are mounted with `API__PREFIX`.
- `permission_registry` and `event/task/schedule` registries are built in `create_app`.

If registry errors exist, app startup fails before serving traffic.
