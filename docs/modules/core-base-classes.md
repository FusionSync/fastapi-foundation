# Core Base Classes

## 职责

Base Classes 模块提供所有 app 必须使用的通用基类和基础工具，保证模型、schema、router 和 service 的行为一致。

## 目录建议

```text
src/core/base/
  models.py
  schemas.py
  routers.py
  routes.py
  services.py
  repositories.py
```

## Model 基类

建议提供：

```text
BaseModel
  id

TimestampMixin
  created_at
  updated_at

SoftDeleteMixin
  deleted_at

TenantScopedModel
  tenant_id

AuditUserMixin
  created_by
  updated_by
```

业务模型按需组合，不允许每个 app 自己重复定义审计字段和租户字段。

## Schema 基类

建议提供：

```text
BaseSchema
  Pydantic 基础配置

ReadSchema
  id
  created_at
  updated_at

CreateSchema
  创建入参基类

UpdateSchema
  更新入参基类

ListQuerySchema
  page
  page_size
  sort
  keyword
```

所有对外 schema 必须继承 core schema 基类，避免序列化规则不一致。

## Route 和 Router 基类

建议提供：

```text
ContextRoute
  进入 route 时初始化/绑定 ContextVar
  记录耗时
  捕获领域异常
  注入 request_id

BaseAPIRouter
  统一 route_class
  统一 tags/prefix 约定
  统一 response envelope
```

业务 app 不直接实例化 FastAPI 原生 `APIRouter`，而是使用 core router 工厂：

```python
router = create_router(prefix="/examples", tags=["examples"])
```

## Service 基类

建议提供：

```text
BaseService
  current_context
  current_user
  current_tenant
  authorize
  publish_event
  run_in_transaction
```

service 默认从 ContextVar 读取请求上下文。需要后台任务或测试时，可以显式传入 context override。

## Repository 基类

Repository 可以作为建议项保留：

```text
BaseRepository
  model
  get_by_id
  list
  create
  update
  soft_delete
  tenant_filter
```

简单 app 可以不写 repository，复杂查询必须从 service 中下沉到 repository。
