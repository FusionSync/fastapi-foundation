# Core Base Classes

## Progress

- Status: `connected`
- Done: BaseModel/BaseSchema/router/service/repository 基线已落地，tenant repository、route security policy、ListQuerySchema 分页/过滤/排序 helper、repository 查询应用 helper、业务 repository 继承 conformance gate 和 service/router 查询绕过 lint 已被上层 gate 或 contract tests 使用。
- Next: _none_

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
  offset
  limit
  sort_terms()
  filter_values()
  to_pagination(total)
```

列表 query schema 应继承 `ListQuerySchema`，并用 `sortable_fields`、`filterable_fields`
和可选 `default_sort` 声明该接口允许的排序和过滤面。业务过滤字段直接作为 schema 字段补充，例如：

```python
from typing import ClassVar


class ExampleListQuery(ListQuerySchema):
    sortable_fields: ClassVar[frozenset[str] | None] = frozenset({"created_at", "title"})
    filterable_fields: ClassVar[frozenset[str] | None] = frozenset({"keyword", "title"})
    default_sort: ClassVar[tuple[str, ...]] = ("-created_at",)

    title: str | None = None
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

`create_router()` 默认创建受保护 router：请求必须已经具备认证主体和租户上下文，否则返回
`AUTH_INVALID_TOKEN` 或 `TENANT_ACCESS_DENIED`。公开接口必须显式声明：

```python
router = create_router(prefix="/health", tags=["health"], public=True)
```

如果 router 声明 `permissions=[...]`，运行时必须在 `app.state.route_authorizer` 挂载授权器。
route dependency 会在认证/租户上下文检查后调用该授权器；未挂载授权器时拒绝请求，避免权限声明只停留在元数据层。

app conformance 会拒绝裸 `APIRouter`，避免业务 app 绕过统一认证、租户和后续权限/限流装配。

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

## Repository / Query 基类

Repository 不是建议项。凡是访问 tenant-scoped model 的业务代码，必须通过 `TenantScopedRepository`、`TenantScopedQuery` 或 core 认可的等价查询构造器。

```text
BaseRepository
  model
  get_by_id
  list
  create
  update
  soft_delete
  tenant_filter

TenantScopedRepository
  自动注入 tenant_id
  自动过滤 deleted_at
  create 时自动写 tenant_id
  apply_list_query

CrossTenantRepository
  仅 platform scope 使用
  强制 reason、permission 和 audit
```

规则：

- 简单 app 也可以复用 core 提供的 generic repository，但不能直接调用 ORM manager。
- service 不直接写 tenant-scoped ORM 查询。
- 列表查询通过 `apply_list_query(statement, query, sort_columns=..., filter_columns=...)`
  应用过滤、排序和分页；字段到 ORM column 的映射必须显式传入。
- raw SQL 必须使用 `core.db.sql` wrapper。
- app conformance 会扫描 app 包内 `repository.py` / `repositories.py`，发现指向 `TenantScopedModel` 的 `*Repository` 未继承 `TenantScopedRepository` 或 `CrossTenantRepository` 时拒绝装载。
- app conformance 会扫描 tenant-scoped 业务 app 的 `services.py` / `service.py` / `router.py`，拒绝 SQLAlchemy 查询 API 导入和直接 `session.execute()` 等查询执行，防止 service/router 绕过 repository。
