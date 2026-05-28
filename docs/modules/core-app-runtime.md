# Core App Runtime

## Progress

- Status: `connected`
- Done: app factory 已串联 config、database runtime、security/rate-limit/metrics/context middleware、app registry、runtime registries、request security、system routes、AppModule lifecycle hooks、lifecycle hook 结构化日志和启动诊断、`serve --run` 启动计划、profile 进程模板和部署产物渲染。
- Next:
  - [ ] 将 runtime registries 和 provider readiness 统一输出为启动诊断摘要。

## 职责

App Runtime 是 FastAPI 应用启动和装配中心，负责创建应用实例、加载模块、注册路由、绑定生命周期、安装中间件和异常处理。

## 目录建议

```text
src/core/app/
  factory.py
  lifespan.py
  middleware.py
  errors.py
  responses.py
```

## 核心能力

- `create_app(settings)` 创建 FastAPI 实例。
- 从 `settings.installed_apps` 加载 app modules，并在启动期强制执行 app conformance check。
- 注册每个 app 的 routers。
- 按 `AppModule.models` 导入 ORM model modules，保证 SQLAlchemy metadata 和后续迁移治理能看到 app 表。
- 基于同一个 `AppRegistry` 装配 permission、migration、event、task、schedule 和 admin registries，并挂到 `app.state`。
- 构建 SQLAlchemy async engine/session factory，挂到 `app.state.database_engine` / `app.state.session_factory`，并在 lifespan shutdown 时释放 engine。
- 注册全局异常处理器。
- 注册请求 ID、日志、CORS、租户上下文等中间件。
- 如果已安装 app 在 `AppModule.auth_session_store` 声明会话事实适配器，`create_app()` 会自动基于 `settings.database.url` 和 `settings.security.jwt_secret` 挂载 HTTP 请求安全流水线。
- 仍可通过 `request_security_pipeline` 显式覆盖默认请求安全流水线。
- 暴露健康检查和版本信息。
- `/readyz` 使用 `check_app_readiness()` 输出 config、database、数据库可连接性、AppRegistry diagnostics、MetricsRegistry 和 lifecycle startup hook 检查明细；不 ready 时返回 HTTP 503。
- `AppModule.lifecycle_hooks` 声明的 startup/shutdown hook 会挂入 FastAPI lifespan；startup 按 dependency-first app 顺序执行，shutdown 按反向顺序执行。
- 每个 lifecycle hook 执行后会写入 `app.state.lifecycle_diagnostics`，并通过 `core.app.lifecycle` logger 输出结构化 `lifecycle_hook` 字段。`/readyz` 会检查 startup hook 是否全部成功，并在 `details.lifecycle_hooks` 中暴露 startup/shutdown hook 结果。

## 不负责

- 不实现具体业务接口。
- 不直接导入 `platform_apps` 或 `apps` 的具体模块。
- 不读取业务配置项。

## 启动入口

```python
from core.app.factory import create_app
from core.config.settings import settings

app = create_app(settings)
```

安装了声明 `auth_session_store` 的账号 app 时，请求安全流水线会自动启用：

```python
app = create_app(
    Settings(
        installed_apps=["platform_apps.accounts.module"],
    )
)
```

需要替换认证 provider 或会话事实来源时，组合方可以显式提供 pipeline 覆盖默认装配：

```python
app = create_app(settings, request_security_pipeline=pipeline)
```

`core serve --run --dry-run` 会走同一个 `create_app()` 装配路径，输出启动计划、route_count 和 server `ProcessHealth`；去掉 `--dry-run` 后由 CLI 使用同一配置启动 Uvicorn。
`core config template --profile <profile> --json` 为 `server`、`worker`、`scheduler`、`outbox-dispatcher` 和 `migrate` 输出统一启动命令、replica 建议和验证命令，后续 profile 部署产物必须从这个矩阵派生。
`core config artifacts --profile <profile> --target <docker-compose|systemd|helm-values> --json` 会把同一个进程矩阵渲染为部署产物内容；每个进程角色会写入自己的 `OBSERVABILITY__SERVICE_ROLE`。传入 `--actual KEY=VALUE` 时复用配置漂移检查，配合 `--role` 可校验单个运行时角色，产物与运行时配置不一致会返回非零 exit code。
生命周期 hook handler 必须正好接受一个 `AppLifecycleContext` 参数。startup hook 失败会阻止 lifespan 启动并释放数据库 runtime；shutdown hook 在数据库释放前执行，失败会以 `RuntimeError` 暴露给运行时。成功和失败都会保留结构化诊断记录，字段包括 `app_label`、`hook_id`、`phase`、`handler_path`、`status` 和可选 `error`。

## 稳定性要求

- app 加载失败必须暴露清晰错误，包含 app path 和异常类型。
- 非合规 app 必须启动失败，不能只依赖 CI 手动执行 `core check-app`。
- `AppModule` 声明的非 router 资源必须在启动期集中装配，不能由业务 app 或各子系统分散注册。
- 生产环境禁止自动建表。
- 启动日志必须输出 env、版本、启用 app 列表、数据库类型和存储 provider。
