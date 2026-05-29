# Core Tenant Isolation

## Progress

- Status: `connected`
- Done: tenant-scoped repository/query、raw SQL guard、跨租户 reason gate、业务唯一约束检查、app model/repository conformance、service/router 静态查询 lint 和 cloud profile 数据库级 RLS/advisory 策略验证已落地。
- Next: _none_

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
- raw SQL 必须声明租户范围，并且 tenant-scoped SQL 必须包含可验证的 `tenant_id = :tenant_id` 谓词。校验必须忽略注释和字符串字面量，并拒绝 `organization_tenant_id` 这类子串列名绕过。

## 跨租户访问

跨租户访问只能用于平台管理、运维、数据导出和合规场景。

必须满足：

- 使用显式 `CrossTenantRepository` 或 `execute_cross_tenant()`。
- 当前用户具备 platform permission，并且调用方必须传入 `AuthorizationService.require_platform()` 返回的 `AuthorizationDecision`；禁止用裸布尔值绕过授权链路。
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
- app conformance 必须扫描 `AppModule.models` 中继承 `TenantScopedModel` 的模型，启动期拒绝缺失租户索引或业务唯一约束未包含 `tenant_id` 的模型。

## Lint 要求

静态检查应覆盖：

- 多租户业务模型缺少 `tenant_id`。
- 业务 app 直接使用裸 SQL。
- router 中直接执行复杂查询。
- Repository 未继承租户基类。
- 跨租户方法未带审计 reason。

当前 app conformance 已在启动期导入 `AppModule.models`，对 `TenantScopedModel` 执行 `check_tenant_scoped_model()`；发现 `tenant_id` 可空、未覆盖索引/约束，或业务 `UniqueConstraint` 未包含 `tenant_id` 时，app 装载失败。conformance 还会扫描 app 包内 `repository.py` / `repositories.py`，拒绝指向 tenant-scoped model 但未继承 `TenantScopedRepository` 或 `CrossTenantRepository` 的 repository。

对声明了 `TenantScopedModel` 的业务 app，conformance 还会静态扫描 `services.py`、`service.py` 和 `router.py`：

- 禁止在 service/router 中导入 SQLAlchemy 查询 API。
- 禁止在 service/router 中直接调用 `session.execute()` / `session.scalar()` / `session.scalars()`。
- 查询必须移动到 `TenantScopedRepository`、`CrossTenantRepository` 或 `core.db.sql` wrapper 后面。
- `platform_apps.*` 作为平台底座实现层暂不套用该业务 app lint；平台 app 仍由模块契约、权限、审计和专门集成测试约束。

## 数据库级兜底

第一版不强制 PostgreSQL RLS，以降低本地和私有化部署复杂度。但公网 SaaS profile 必须启用 PostgreSQL session variable 兜底，并由 `check-config` 输出可审计的 RLS/advisory 策略验证：

```text
DATABASE__TENANT_FALLBACK_MODE=session_variable
DATABASE__TENANT_FALLBACK_SETTING_NAME=app.tenant_id
```

`core.db.verify_database_tenant_guard()` 会验证 cloud profile 使用 PostgreSQL、启用 `session_variable` fallback，并生成每张租户表的 RLS policy plan：

```sql
ALTER TABLE bid_records ENABLE ROW LEVEL SECURITY
ALTER TABLE bid_records FORCE ROW LEVEL SECURITY
CREATE POLICY bid_records_tenant_isolation ON bid_records
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
```

同一报告还输出事务级 advisory lock 策略：

```sql
SELECT pg_advisory_xact_lock(hashtext(current_setting('app.tenant_id', true)))
```

即使启用 RLS，也不能移除 repository/query guard；RLS 是生产兜底，不是业务层权限模型。
