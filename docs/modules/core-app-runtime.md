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
- 从 `settings.installed_apps` 加载 app modules。
- 注册每个 app 的 routers。
- 构建 Tortoise ORM 配置并绑定生命周期。
- 注册全局异常处理器。
- 注册请求 ID、日志、CORS、租户上下文等中间件。
- 暴露健康检查和版本信息。

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

## 稳定性要求

- app 加载失败必须暴露清晰错误，包含 app path 和异常类型。
- 生产环境禁止自动建表。
- 启动日志必须输出 env、版本、启用 app 列表、数据库类型和存储 provider。
