# Core Database

## 职责

Database 模块负责 ORM 配置、连接管理、基础模型、迁移约定和事务工具。

## ORM 选择

当前选择 Tortoise ORM，原因：

- 原生 async，适配 FastAPI。
- 模型声明和查询方式直接，团队学习成本低。
- 对 CRUD、过滤、分页、关联加载足够实用。
- 复杂 SQL 可以通过 raw query 或专用 repository 补足。

## 目录建议

```text
src/core/db/
  config.py
  init.py
  base.py
  transactions.py
  pagination.py
```

## 基础模型约定

建议提供以下 abstract model/mixin：

```text
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

## 多租户约束

- 业务表默认包含 `tenant_id`。
- 查询服务必须从租户上下文取 `tenant_id`。
- 禁止在 router 中直接写复杂查询。
- 跨租户管理接口必须放在平台管理命名空间，并有独立权限。

## 迁移策略

- 每个 app 管理自己的 models 和 migrations。
- core 负责收集 app ORM 配置。
- 生产环境通过 migration 命令升级，不在启动时自动建表。

## 数据库选择

```text
local:
  SQLite 或 PostgreSQL

private/cloud:
  PostgreSQL
```

SQLite 仅用于单机版和演示，不作为多人协作生产方案。
