# Core Config

## Progress

- Status: `connected`
- Done: settings、profile 校验、secret provider、HTTP client credential secret ref、脱敏诊断、启动期安全检查、local/private/cloud profile 模板输出、task queue/scheduler/tenant lifecycle 运行参数、profile security hardening 清单、配置 drift-check、运行时漂移告警输出、profile 派生部署产物渲染和 release checkpoint drift gate 已落地。
- Next: _none_

## 职责

Config 模块是全局配置入口，负责从环境变量、`.env` 和部署 profile 中加载配置，并向 core 和 apps 提供统一 settings 对象。

## 目录建议

```text
src/core/config/
  settings.py
  profiles.py
  validation.py
```

## 配置边界

所有基础设施配置放在 core：

- `AppSettings`
- `DatabaseSettings`
- `AuthSettings`
- `PermissionSettings`
- `StorageSettings`
- `TaskSettings`
- `CorsSettings`
- `LoggingSettings`
- `ObservabilitySettings`

业务 app 可以定义自己的配置 schema，但必须通过 `Settings` 聚合，不允许 app 内部直接读取 `os.environ`。

## 环境变量约定

使用 `__` 做嵌套分隔：

```env
DATABASE__URL=postgres://user:pass@localhost:5432/service_core
DATABASE__READ_URL=postgres://readonly:pass@localhost:5432/service_core
DATABASE__POOL_SIZE=10
DATABASE__MAX_OVERFLOW=20
DATABASE__TENANT_FALLBACK_MODE=session_variable
DATABASE__TENANT_FALLBACK_SETTING_NAME=app.tenant_id
AUTH__PROVIDER=local_jwt
AUTH__JWT_SECRET=change-me
SECURITY__JWT_SECRET_REF=APP_JWT_SECRET
SECURITY__TRUSTED_HOSTS='["api.example.com"]'
SECURITY__CORS_ORIGINS='["https://console.example.com"]'
STORAGE__PROVIDER=local
STORAGE__LOCAL_ROOT=./data/files
TASK_QUEUE__PROVIDER=sync
SCHEDULER__IDLE_SLEEP_SECONDS=1.0
TENANT_LIFECYCLE__ALLOW_SUSPENDED_FILE_DOWNLOAD=false
TENANT_LIFECYCLE__ALLOW_ARCHIVED_READ=false
TENANT_LIFECYCLE__ALLOW_ARCHIVED_FILE_DOWNLOAD=false
```

## 部署 profile

```text
local
  SQLite 或本地 PostgreSQL，本地文件存储，同步任务，本地 JWT

private
  PostgreSQL，MinIO，本地或内网依赖服务，Keycloak 或 Local JWT

cloud
  PostgreSQL，S3/MinIO，Redis，Logto 或 OIDC，异步任务队列
```

## 设计要求

- 默认配置必须能启动本地开发环境。
- 生产环境必须校验关键密钥不可使用默认值。
- 配置对象应可序列化为脱敏诊断信息。
- 业务 app 的配置必须显式声明，不能动态散落。
- 配置优先级必须固定：显式环境变量 > secret provider > profile 文件 > `.env` > 默认值。
- `private` 和 `cloud` profile 必须通过 `core check-config --profile <name> --json` 校验后才能启动。
- secret provider 第一版至少支持 env/Kubernetes Secret/Vault-like adapter 的接口，具体实现可分阶段。
- 诊断输出必须脱敏 URL password、token、secret、private key。

## Profile Template

`core config template --profile <local|private|cloud> --json` 输出可验证的部署配置模板：

- `env` 给出该 profile 的环境变量键和值或占位符。
- `processes` 给出 `server`、`worker`、`scheduler`、`outbox-dispatcher`、`migrate` 的启动命令、replica 建议和运行备注。
- `security_hardening` 给出该 profile 必须核对的 CSP、cookie、TLS/HSTS 和响应头控制项。
- `monitoring` 给出该 profile 的 dashboard panels 和 alert rules 契约。
- `validation_commands` 给出发布脚本可直接执行的检查命令，包括 `check-config`、`config drift-check`、`serve --run --dry-run`、`migrate run` 和 `smoke`。

模板中的生产密钥通过 `SECURITY__JWT_SECRET_REF` 引用外部 secret，不输出 `SECURITY__JWT_SECRET` 明文。private/cloud 模板默认使用 PostgreSQL URL 占位符、database task queue provider 和标准 HTTP status mode。
数据库配置支持 `DATABASE__READ_URL` 预留只读连接，`DATABASE__POOL_SIZE` / `DATABASE__MAX_OVERFLOW` 控制 SQLAlchemy pool 参数，`DATABASE__TENANT_FALLBACK_MODE=session_variable` 可启用 PostgreSQL session variable 租户兜底。
worker 通过 `TASK_QUEUE__PROVIDER`、`TASK_QUEUE__MAX_ATTEMPTS`、`TASK_QUEUE__RETRY_BACKOFF_SECONDS`、`TASK_QUEUE__IDLE_SLEEP_SECONDS` 参数化；scheduler 通过 `SCHEDULER__IDLE_SLEEP_SECONDS` 和 `SCHEDULER__LOCK_TTL_SECONDS` 参数化。tenant lifecycle 策略通过 `TENANT_LIFECYCLE__ALLOW_SUSPENDED_FILE_DOWNLOAD`、`TENANT_LIFECYCLE__ALLOW_ARCHIVED_READ` 和 `TENANT_LIFECYCLE__ALLOW_ARCHIVED_FILE_DOWNLOAD` 参数化，默认均为 `false`。
private/cloud 模板的 hardening 清单默认要求生产 ingress 或反向代理启用 CSP、Secure/HttpOnly/SameSite cookie、HSTS 和安全响应头；cloud profile 的 HSTS evidence 额外包含 `preload`。
`check-config` 会接受生产 profile 的外部 secret reference，但启动期 `create_app()` 仍必须通过 secret provider 解析到真实密钥后才能通过 `validate_startup_settings()`。

## Config Drift Check

`core config drift-check --profile <profile> --json` 会把实际环境变量与 profile 模板中的 `env` 对比；也可以通过重复 `--actual KEY=VALUE` 显式传入待检查环境，便于 CI/CD 在不读取宿主环境的情况下验证发布参数。
校验具体进程角色时传 `--role <server|worker|scheduler|outbox-dispatcher|migrate>`，`OBSERVABILITY__SERVICE_ROLE` 会按角色期望值检查，而不是固定使用 profile 的默认 server 值。

漂移报告输出：

- `checked`：模板要求检查的环境变量。
- `missing`：实际环境缺失的必需项。
- `mismatched`：实际值与模板不一致的项。
- `alerts`：发现漂移时输出 `ConfigDriftDetected` 告警事件，包含 profile、role、缺失/不匹配计数和可执行 runbook；告警 annotations 不包含实际 secret 或数据库密码。

模板值中的 `${PLACEHOLDER}` 会作为非空通配符处理，因此 `DATABASE__URL=postgresql+asyncpg://app:${DATABASE_PASSWORD}@postgres:5432/wps_bid` 可匹配运行时注入的真实密码。报告中的数据库 URL password、secret、token、password 字段必须脱敏。

## Deployment Artifacts

`core config artifacts --profile <profile> --target <docker-compose|systemd|helm-values> --json` 从同一个 profile template 派生部署文件内容，输出：

- `files`：待写入部署仓库或安装包的文件名和内容。
- `validation_commands`：发布脚本必须执行的校验命令，包含 `check-config`、`config drift-check`、`serve --run --dry-run`、`migrate run` 和 `smoke`。
- `source_template_command` / `drift_check_command`：产物来源和配置漂移校验入口。

部署产物会保留 profile template 中的 security hardening 清单：Docker Compose 使用 `x-security-hardening`，Helm values 使用 `securityHardening`，systemd env 示例使用注释块承载同一组控制项。
部署产物也会保留 profile template 中的 monitoring 契约：Docker Compose 使用 `x-monitoring`，Helm values 使用 `monitoring`，systemd env 示例使用注释块列出告警规则。

如果同时传入重复 `--actual KEY=VALUE`，命令会在输出产物时执行同一套 drift-check；发现缺失或不匹配时返回 exit code `1`，并在 `drift` 中输出脱敏报告。配合 `--role` 使用时，校验对象是该进程角色的运行时环境。

## Release Checkpoint Drift Gate

`core release checkpoint --profile <profile> --artifact-target <target> --json` 会把 profile template、部署产物、配置校验、备份就绪、按角色 drift-check、migration dry-run 和 smoke 汇总成一个发布 gate。传入重复 `--actual KEY=VALUE` 作为公共候选环境，或传 `--role-actual ROLE:KEY=VALUE` 覆盖单个进程角色环境；任一角色漂移都会让 checkpoint 返回 exit code `1`。

## Secret Provider

`Settings.security.jwt_secret_ref` 可以声明外部密钥引用。`create_app(settings, secret_provider=...)` 会先调用 `resolve_settings_secrets()`，再执行启动校验。外部 HTTP client 使用 `HttpClientCredentialSpec.secret_ref` 声明 credential 引用，并通过同一个 `SecretProvider` 协议在请求时解析。

第一版提供：

- `EnvSecretProvider`：从环境变量读取 secret。
- `MappingSecretProvider`：用于测试、local profile 或上层系统注入。

如果 `jwt_secret` 已显式配置为非默认值，secret provider 不覆盖它；如果仍是默认 `change-me` 且配置了 `jwt_secret_ref`，provider 必须能解析到 secret，否则启动失败。
