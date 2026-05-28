# App Module Contract

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
from core.apps.module import AppModule, EventHandlerSpec, MigrationSpec, ScheduleSpec, TaskHandlerSpec
from core.permissions import PermissionSpec
from .router import router

module = AppModule(
    label="example_domain",
    version="0.1.0",
    dependencies=["platform_accounts"],
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
    public_api=[],
)
```

## 注册项类型

`AppModule` 不接受裸 `dict` 或任意对象注册事件、任务和调度。必须使用：

- `EventHandlerSpec(event_type, event_version, handler_path)`
- `TaskHandlerSpec(task_type, handler_path, queue)`
- `ScheduleSpec(schedule_id, task_type, trigger, trigger_config, misfire_policy)`
- `AdminModelSpec(admin_id, model_path, label, permissions, tenant_scoped, read_only)`
- `AdminRouteSpec(route_id, path, handler_path, permissions, methods)`
- `AdminDashboardWidgetSpec(widget_id, title, provider_path, permissions)`
- `AdminPermissionSpec(resource, action, description, risk_level)`

这样 app contract check 可以在启动前发现拼写错误、空 handler path 和不合法版本。
后台相关 spec 还会校验 `/admin` 路由边界、平台级权限边界和重复注册风险。

## 依赖方向

```text
apps -> platform_apps -> core
apps -> core
core -> no app imports
```

业务 app 可以依赖平台 app 的公开 service，不能导入其他 app 的内部 repository、models 实现。
平台 app 也必须按标准结构提供 `module.py`、`schemas.py`、`models.py`、`router.py`、`services.py`、`permissions.py` 和 `migrations/`。
当前底座内置的 `platform_apps.accounts.module`、`platform_apps.audit.module`、`platform_apps.files.module` 都可被 `AppRegistry` 直接加载。

允许的跨 app 调用：

```text
apps.foo -> platform_apps.accounts.public_api
apps.foo -> apps.bar.public_api
apps.foo -> core events/tasks/interfaces
```

禁止的跨 app 调用：

```text
apps.foo -> apps.bar.models
apps.foo -> apps.bar.repository
apps.foo -> platform_apps.tenants.models
```

## 最小要求

- 每个 app 必须有稳定 label。
- 每个 app 必须声明 version 和 dependencies。
- 每个 app 必须声明自己的权限点。
- 每个 app 的数据模型必须明确是否租户隔离。
- 每个 app 的外部接口必须遵守 API conventions。
- 每个 app 必须使用标准文件名：`schemas.py`、`models.py`、`router.py`、`services.py`。
- 每个 app router 必须通过 `core.base.create_router()` 创建；匿名公开接口必须显式声明 `public=True`。
- 每个 app 的 migrations、tasks、events、schedules 必须通过 `AppModule` 注册。
- app contract check 必须拒绝循环依赖、非法导入和缺失标准文件。
- app registry 必须按 dependency-first 顺序装载模块；业务代码不能依赖 `settings.installed_apps` 的人工顺序来规避缺失依赖声明。
