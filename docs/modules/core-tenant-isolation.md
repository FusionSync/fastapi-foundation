# Core Tenant Isolation

## 职责

Tenant Isolation 定义多租户数据隔离的强制机制。成熟底座不能依赖“开发者记得过滤 `tenant_id`”，必须通过模型、Repository、SQL 封装、测试和审计共同保证。

## 隔离层级

第一版采用 shared database + shared schema：

```text
所有租户共享数据库和 schema
所有租户业务表带 tenant_id
所有查询默认注入 tenant_id
跨租户访问必须显式声明
```

后续可选增强：

```text
PostgreSQL Row Level Security
schema-per-tenant
database-per-tenant
```

第一版不默认使用 schema-per-tenant，避免迁移和运维复杂度过高。

## 强制机制

必须提供：

```text
TenantScopedModel
TenantScopedRepository
TenantScopedQuery
CrossTenantRepository
core.db.sql.execute_tenant_scoped()
core.db.sql.execute_cross_tenant()
```

访问 tenant-scoped model 的默认路径必须是强制路径，不是代码风格建议。业务 app 直接调用 ORM manager、裸 SQL 或跨 app repository 访问 tenant-scoped 数据，都应被 lint、contract test 或 runtime guard 拒绝。

默认行为：

- `TenantScopedRepository.list()` 自动过滤当前 `tenant_id`。
- `TenantScopedRepository.get()` 自动过滤当前 `tenant_id`。
- `TenantScopedRepository.create()` 自动写入当前 `tenant_id`。
- 默认过滤 `deleted_at is null`。
- raw SQL 必须声明租户范围。

## 跨租户访问

跨租户访问只能用于平台管理、运维、数据导出和合规场景。

必须满足：

- 使用显式 `CrossTenantRepository` 或 `execute_cross_tenant()`。
- 当前用户具备 platform permission。
- 调用方提供 reason。
- 写入审计日志。
- 返回结果必须限制字段，避免无意暴露敏感数据。

## 数据库约束

租户业务表必须遵守：

```text
tenant_id NOT NULL
tenant_id FK -> tenants.id
```

常用索引：

```text
(tenant_id, id)
(tenant_id, created_at)
(tenant_id, deleted_at, created_at)
```

业务唯一约束必须租户化：

```text
UNIQUE (tenant_id, code)
UNIQUE (tenant_id, external_id)
```

## 测试要求

core testing 必须提供租户隔离契约测试：

- A 租户不能读取 B 租户数据。
- A 租户不能更新 B 租户数据。
- 软删除数据默认不可见。
- raw SQL 未声明租户范围时测试失败。
- 跨租户查询没有 reason 时测试失败。

## Lint 要求

静态检查应覆盖：

- 多租户业务模型缺少 `tenant_id`。
- 业务 app 直接使用裸 SQL。
- router 中直接执行复杂查询。
- Repository 未继承租户基类。
- 跨租户方法未带审计 reason。

## 数据库级兜底

第一版不强制 PostgreSQL RLS，以降低本地和私有化部署复杂度。但公网 SaaS profile 应预留 RLS 开关：

```text
TENANCY__DATABASE_GUARD=repository
TENANCY__DATABASE_GUARD=repository_plus_rls
```

即使启用 RLS，也不能移除 repository/query guard；RLS 是生产兜底，不是业务层权限模型。
