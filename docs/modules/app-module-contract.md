# App Module Contract

## 目标

App Module Contract 定义业务 app 如何接入框架。任何业务能力都必须遵守这个契约，避免直接修改 core 启动逻辑。

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
from core.apps.module import AppModule, MigrationSpec
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
    event_handlers=[],
    task_handlers=[],
    schedules=[],
    public_api=[],
)
```

## 依赖方向

```text
apps -> platform_apps -> core
apps -> core
core -> no app imports
```

业务 app 可以依赖平台 app 的公开 service，不能导入其他 app 的内部 repository、models 实现。

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
- 每个 app 的 migrations、tasks、events、schedules 必须通过 `AppModule` 注册。
- app contract check 必须拒绝循环依赖、非法导入和缺失标准文件。
