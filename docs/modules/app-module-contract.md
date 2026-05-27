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
  service.py
  repository.py
  permissions.py
  events.py
  tasks.py
  migrations/
  tests/
```

## module.py

```python
from core.apps.module import AppModule
from .router import router

module = AppModule(
    label="example_domain",
    routers=[router],
    orm_apps={
        "example_domain": {
            "models": ["apps.example_domain.models"],
            "migrations": "apps.example_domain.migrations",
        }
    },
    permissions=[
        ("example", "read"),
        ("example", "write"),
    ],
    event_handlers=[],
    task_handlers=[],
)
```

## 依赖方向

```text
apps -> platform_apps -> core
apps -> core
core -> no app imports
```

业务 app 可以依赖平台 app 的公开 service，不能导入其他 app 的内部 repository、models 实现。

## 最小要求

- 每个 app 必须有稳定 label。
- 每个 app 必须声明自己的权限点。
- 每个 app 的数据模型必须明确是否租户隔离。
- 每个 app 的外部接口必须遵守 API conventions。
