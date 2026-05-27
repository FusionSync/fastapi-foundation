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
