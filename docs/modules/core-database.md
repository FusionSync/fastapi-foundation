# Core Database

## 职责

Database 模块负责 ORM 配置、连接管理、基础模型、迁移约定和事务工具。

## ORM 选择

当前选择 SQLAlchemy 2.x async + Alembic，原因：

- SQLAlchemy 事务、连接、Unit of Work 和复杂查询能力成熟。
- Alembic 迁移生态成熟，适合做生产 preflight、drift check 和 expand-contract。
- async engine/session 适配 FastAPI。
- SQLAlchemy Core 可承载复杂报表和 PostgreSQL 专有能力。
- 业务 app 仍通过 core repository/unit-of-work 使用数据库，避免直接依赖底层 session。

## 目录建议

```text
src/core/db/
  config.py
  init.py
  base.py
  transactions.py
  pagination.py
  repositories.py
  sql.py
```

## 事务工具

必须提供 unit-of-work 风格事务入口，保证业务写入、outbox 写入和审计写入可以共享同一个数据库连接：

```text
async with unit_of_work() as uow:
  await repo.create(..., session=uow.session)
  await outbox.add(..., session=uow.session)
```

约束：

- repository 方法必须接受或绑定同一个 `AsyncSession`。
- outbox 和安全关键 audit 禁止在业务事务外隐式写入新连接。
- 嵌套 service 调用复用外层 unit-of-work；第一版不做复杂嵌套事务编排。
- transaction helper 必须在 rollback 时保证 outbox/audit 同步回滚。

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
- 租户表必须使用 `TenantScopedRepository` 或等价查询构造器。
- 默认查询必须自动追加 `tenant_id = current_tenant.id` 和 `deleted_at is null`。
- 跨租户查询必须使用显式 `CrossTenantRepository` 或 `execute_cross_tenant()`，并要求 platform `AuthorizationDecision`、reason 和审计；入口不接受裸布尔值作为授权证明。
- raw SQL 必须走 `core.db.sql` 受控封装，禁止业务 app 直接裸执行 SQL；tenant-scoped SQL 必须包含显式 `tenant_id = :tenant_id` 谓词，不能只依赖参数注入或注释/子串命中。
- 禁止在 router 中直接写复杂查询。
- 跨租户管理接口必须放在平台管理命名空间，并有独立权限。

## 数据库约束约定

租户业务表必须遵守：

```text
tenant_id NOT NULL
tenant_id 外键指向 tenants.id
常用查询索引包含 tenant_id
业务唯一键使用 tenant scoped unique
```

常见索引模式：

```text
(tenant_id, id)
(tenant_id, business_key)
(tenant_id, deleted_at, created_at)
```

禁止在多租户业务表上定义全局唯一业务键，除非该字段确实是全平台唯一。

## 迁移策略

- 每个 app 管理自己的 models 和 migrations。
- core 负责收集 app ORM 配置。
- 生产环境通过 migration 命令升级，不在启动时自动建表。
- 迁移治理细则见 [Migrations](core-migrations.md)。

## 数据库选择

```text
local:
  SQLite 或 PostgreSQL

private/cloud:
  PostgreSQL
```

SQLite 仅用于单机版和演示，不作为多人协作生产方案。
