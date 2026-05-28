# Core App Runtime

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
- 构建 SQLAlchemy async engine/session factory 并绑定生命周期。
- 注册全局异常处理器。
- 注册请求 ID、日志、CORS、租户上下文等中间件。
- 可通过 `request_security_pipeline` 挂载 HTTP 请求安全流水线，把 Bearer token、session/user fact、tenant resolver 和 route permissions 接入 route dependency。
- 暴露健康检查和版本信息。
- `/readyz` 使用 `check_app_readiness()` 输出 config、database、数据库可连接性、AppRegistry、MetricsRegistry 检查明细；不 ready 时返回 HTTP 503。

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

接入数据库请求安全流水线时，组合方提供 session factory、JWT provider 和账号 session store：

```python
app = create_app(settings, request_security_pipeline=pipeline)
```

## 稳定性要求

- app 加载失败必须暴露清晰错误，包含 app path 和异常类型。
- 非合规 app 必须启动失败，不能只依赖 CI 手动执行 `core check-app`。
- `AppModule` 声明的非 router 资源必须在启动期集中装配，不能由业务 app 或各子系统分散注册。
- 生产环境禁止自动建表。
- 启动日志必须输出 env、版本、启用 app 列表、数据库类型和存储 provider。
