# App Module Contract

## Progress

- Status: `connected`
- Done: typed `AppModule`、core version/capability metadata、依赖图、标准文件、router security、response envelope、public_api 边界、admin/migration metadata 细化诊断、background/lifecycle handler 签名和 tenant model conformance 已接入启动检查。
- Next: _none_

## 目标

App Module Contract 定义业务 app 和 platform app 如何接入框架。任何业务能力都必须遵守这个契约，避免直接修改 core 启动逻辑。

## 标准目录

```text
src/apps/example_domain/
  module.py
  models.py
  schemas.py
  router.py
  services.py
  repository.py
  permissions.py
  events.py
  tasks.py
  migrations/
  tests/
```

## module.py

```python
from core.admin import AdminModelSpec, AdminPermissionSpec
from core.apps.module import AppModule, EventHandlerSpec, LifecycleHookSpec, MigrationSpec, ScheduleSpec, TaskHandlerSpec
from core.permissions import PermissionSpec
from .router import router

module = AppModule(
    label="example_domain",
    version="0.1.0",
    min_core_version="0.1.0",
    dependencies=["platform_accounts"],
    required_capabilities=["tasks"],
    provided_capabilities=["example_domain.public_api"],
    routers=[router],
    models=["apps.example_domain.models"],
    migrations=MigrationSpec(
        path="apps.example_domain.migrations",
        depends_on=[],
    ),
    permissions=[
        PermissionSpec(resource="example", action="read", scope="tenant"),
        PermissionSpec(resource="example", action="write", scope="tenant"),
    ],
    event_handlers=[
        EventHandlerSpec(
            event_type="example.created",
            event_version=1,
            handler_path="apps.example_domain.events.handle_example_created",
        )
    ],
    task_handlers=[
        TaskHandlerSpec(
            task_type="example.refresh",
            handler_path="apps.example_domain.tasks.refresh_example",
            queue="default",
        )
    ],
    schedules=[
        ScheduleSpec(
            schedule_id="example.refresh.daily",
            task_type="example.refresh",
            trigger="cron",
            trigger_config={"hour": "1"},
            misfire_policy="skip",
        )
    ],
    lifecycle_hooks=[
        LifecycleHookSpec(
            hook_id="warmup-cache",
            phase="startup",
            handler_path="apps.example_domain.lifecycle.warmup_cache",
        ),
        LifecycleHookSpec(
            hook_id="flush-cache",
            phase="shutdown",
            handler_path="apps.example_domain.lifecycle.flush_cache",
        ),
    ],
    admin_models=[
        AdminModelSpec(
            admin_id="example.items",
            model_path="apps.example_domain.models.ExampleItem",
            label="Example Items",
            permissions=[
                AdminPermissionSpec(resource="example_items", action="read"),
            ],
            tenant_scoped=True,
            read_only=True,
        )
    ],
    auth_session_store=None,
    public_api=[],
)
```

## 注册项类型

`AppModule` 不接受裸 `dict` 或任意对象注册事件、任务和调度。必须使用：

- `EventHandlerSpec(event_type, event_version, handler_path)`
- `TaskHandlerSpec(task_type, handler_path, queue)`
- `ScheduleSpec(schedule_id, task_type, trigger, trigger_config, misfire_policy)`
- `LifecycleHookSpec(hook_id, phase, handler_path)`
- `AdminModelSpec(admin_id, model_path, label, permissions, tenant_scoped, read_only)`
- `AdminRouteSpec(route_id, path, handler_path, permissions, methods)`
- `AdminDashboardWidgetSpec(widget_id, title, provider_path, permissions)`
- `AdminPermissionSpec(resource, action, description, risk_level)`

这样 app contract check 可以在启动前发现拼写错误、空 handler path 和不合法版本。
后台相关 spec 还会校验 `/admin` 路由边界、平台级权限边界和重复注册风险。
app contract check 会导入 admin metadata 中声明的 `AdminModelSpec.model_path`、`AdminRouteSpec.handler_path` 和 `AdminDashboardWidgetSpec.provider_path`；不可导入、不可调用或指向错误对象时，错误会包含 admin 类型、id 和 dotted path。
event/task handler 必须是可导入 callable，并且签名必须正好接受一个 envelope 参数；不符合运行时契约的 handler 会在 `check_app()` 或 app factory 启动检查中失败。
lifecycle hook handler 必须是可导入 callable，并且签名必须正好接受一个 context 参数；startup hook 失败会阻止应用 lifespan 启动，shutdown hook 会在数据库 runtime 释放前按反向依赖顺序执行。

`auth_session_store` 是少数由 app 向 core runtime 暴露的装配钩子，值必须是可导入 callable 路径，例如 `platform_apps.accounts.public_api.AccountsAuthSessionStore`。同一运行时只能安装一个声明该字段的 app。
`min_core_version` 和 `required_capabilities` 是启动前 gate：AppRegistry 会在依赖排序后拒绝 core 版本过低或 runtime capability 缺失的 app，并把失败原因写入 registry diagnostics。runtime capability 来自当前 Settings、部署 profile、进程 role 和已配置 provider，例如 `profile.cloud`、`provider.database.postgresql`、`provider.auth.external_secret` 或 `observability.metrics`。`provided_capabilities` 只表达 app 对外提供的能力标签，用于诊断和后续 capability 发现，不替代 dependencies。

## 依赖方向

```text
apps -> platform_apps -> core
apps -> core
core -> no app imports
```

业务 app 可以依赖平台 app 的公开 service，但必须在 `dependencies` 中声明对应平台 app label；不能导入其他 app 的内部 repository、models 实现。
平台 app 也必须按标准结构提供 `module.py`、`schemas.py`、`models.py`、`router.py`、`services.py`、`permissions.py` 和 `migrations/`。
当前底座内置的 `platform_apps.accounts.module`、`platform_apps.audit.module`、`platform_apps.files.module`、`platform_apps.tenants.module` 都可被 `AppRegistry` 直接加载。

允许的跨 app 调用：

```text
apps.foo -> platform_apps.accounts.public_api
apps.foo -> apps.bar.public_api
apps.foo -> core events/tasks/interfaces
```

`apps.foo -> platform_apps.accounts.public_api` 必须同时声明 `dependencies=["platform_accounts"]`；`apps.foo -> apps.bar.public_api` 必须声明 `dependencies=["bar"]`。

禁止的跨 app 调用：

```text
apps.foo -> apps.bar.models
apps.foo -> apps.bar.repository
apps.foo -> platform_apps.tenants.models
```

## 最小要求

- 每个 app 必须有稳定 label。
- 每个 app 必须声明 version 和 dependencies。
- 需要特定 core 能力或 provider 的 app 必须声明 `min_core_version` / `required_capabilities`，不能在启动后才隐式失败。
- 每个 app 必须声明自己的权限点。
- 每个 app 的数据模型必须明确是否租户隔离。
- 每个 app 的外部接口必须遵守 API conventions。
- 每个 app 必须使用标准文件名：`schemas.py`、`models.py`、`router.py`、`services.py`。
- 每个 app router 必须通过 `core.base.create_router()` 创建；匿名公开接口必须显式声明 `public=True`。
- 每个进入 OpenAPI 的 JSON route 必须声明 `response_model=Envelope[ReadSchema]` 或 `response_model=ListEnvelope[ReadSchema]`；文件下载和流式响应必须显式声明 `response_class=FileResponse` 或 `response_class=StreamingResponse` 才能跳过 JSON envelope。
- 每个 app 的 migrations、tasks、events、schedules 必须通过 `AppModule` 注册。
- 需要参与启动或关闭流程的 app 必须通过 `LifecycleHookSpec` 注册 startup/shutdown hook，不能在 core app factory 中硬编码业务 app 初始化逻辑。
- 提供账号会话事实的 app 必须通过 `auth_session_store` 声明 `AuthSessionStore` factory，不允许在 core app factory 中硬编码具体账号 app。
- app contract check 必须拒绝循环依赖、非法导入和缺失标准文件。
- app contract check 必须拒绝未声明 dependency 的 `apps.*.public_api` 和 `platform_apps.*.public_api` 导入。
- app contract check 必须拒绝不可导入、不可调用或签名不符合一个 envelope 参数契约的 event/task handler。
- app contract check 必须拒绝不可导入、不可调用或签名不符合一个 context 参数契约的 lifecycle hook。
- app contract check 必须拒绝不可导入的 admin metadata dotted path，以及 app_label 不匹配、类型错误、重复 key 或 `MigrationManifest.validate()` 不通过的 migration metadata。
- app contract check 必须扫描 `AppModule.models` 中的 `TenantScopedModel` 约束，拒绝全局唯一键等会破坏租户隔离的数据模型。
- app registry 必须按 dependency-first 顺序装载模块；业务代码不能依赖 `settings.installed_apps` 的人工顺序来规避缺失依赖声明。
