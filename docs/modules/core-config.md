# Core Config

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
AUTH__PROVIDER=local_jwt
AUTH__JWT_SECRET=change-me
SECURITY__JWT_SECRET_REF=APP_JWT_SECRET
SECURITY__TRUSTED_HOSTS='["api.example.com"]'
SECURITY__CORS_ORIGINS='["https://console.example.com"]'
STORAGE__PROVIDER=local
STORAGE__LOCAL_ROOT=./data/files
TASKS__PROVIDER=sync
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

## Secret Provider

`Settings.security.jwt_secret_ref` 可以声明外部密钥引用。`create_app(settings, secret_provider=...)` 会先调用 `resolve_settings_secrets()`，再执行启动校验。

第一版提供：

- `EnvSecretProvider`：从环境变量读取 secret。
- `MappingSecretProvider`：用于测试、local profile 或上层系统注入。

如果 `jwt_secret` 已显式配置为非默认值，secret provider 不覆盖它；如果仍是默认 `change-me` 且配置了 `jwt_secret_ref`，provider 必须能解析到 secret，否则启动失败。
