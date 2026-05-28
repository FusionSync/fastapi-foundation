# Core Migrations

## 职责

Migrations 模块负责数据库迁移治理。成熟底座不能只要求“每个 app 有 migrations”，还需要统一迁移注册、依赖顺序、预检查、回滚策略和生产发布流程。

## 为什么迁移治理重要

数据库迁移是生产系统最容易出事故的地方：

- 大表加字段可能锁表。
- 删除字段可能让旧代码崩溃。
- 唯一索引可能因脏数据创建失败。
- 多个 app 的迁移可能有依赖顺序。
- 回滚代码不等于数据库能回滚。

所以迁移必须有流程和门禁。

## 目录建议

```text
src/core/migrations/
  registry.py
  planner.py
  preflight.py
  drift.py
  runner.py
```

## 迁移注册

每个 app 在 `module.py` 中声明：

```text
migrations.path
migrations.depends_on
```

core 收集后生成迁移计划：

```text
load app modules
  -> collect migration metadata
  -> build dependency graph
  -> topological sort
  -> run preflight
  -> run migrations
```

## Migration Manifest

每个 migration 必须提供机器可读 metadata，不能只靠文件名和人工描述：

```text
app_label
migration_id
phase
classification
depends_on
estimated_rows
lock_risk
backfill_required
backfill_plan
rollback_strategy
approved_by
approved_at
```

字段约束：

- `phase`：`expand`、`backfill`、`contract`、`maintenance`。
- `classification`：`reversible`、`forward_only`、`destructive`、`requires_backup_restore`。
- destructive 和 requires_backup_restore 必须有 approval 和备份检查。
- 大表变更必须声明 lock_risk 和 backfill_plan。
- 兼容性变更必须说明支持的新旧代码版本范围。

## 发布流程

生产发布必须按流程：

```text
1. CI 生成并检查 migration
2. schema drift check
3. migration dry-run
4. preflight 检查数据和锁风险
5. 备份或确认备份可用
6. 执行 expand migration
7. 部署兼容新旧 schema 的代码
8. backfill 数据
9. 验证业务
10. 执行 contract migration
```

## Expand-Contract 策略

破坏性变更必须拆多步。

示例：重命名字段 `name` -> `display_name`

```text
版本 1：
  添加 display_name，可空
  代码同时写 name 和 display_name

版本 2：
  backfill display_name
  代码读取 display_name，兼容 name

版本 3：
  display_name 改 NOT NULL
  停止写 name

版本 4：
  删除 name
```

禁止在同一个发布中直接删除旧字段并切代码。

## Preflight 检查

迁移执行前检查：

- 当前数据库版本。
- app migration 依赖是否满足。
- 是否存在 schema drift。
- 目标表行数和锁风险。
- 唯一索引是否会因重复数据失败。
- NOT NULL 字段是否有默认值或 backfill 计划。
- 是否包含 destructive operation。

## 回滚策略

不是所有迁移都应该回滚。文档必须标记：

```text
reversible
forward_only
destructive
requires_backup_restore
```

成熟系统优先采用 forward fix：

```text
发现问题
  -> 不直接回滚数据库
  -> 发布修复 migration 或兼容代码
```

## SQLAlchemy/Alembic 约束

使用 SQLAlchemy 和 Alembic 时：

- 复杂 PostgreSQL 能力允许手写 SQL migration。
- partial index、RLS、分区表、复杂约束必须人工 review。
- 自动生成 migration 不能直接进入生产，必须补齐 manifest 并通过 preflight。
- CI 必须执行 schema drift check。
- 生产启动禁止自动建表或隐式迁移。
- Alembic revision 必须绑定 AppModule migration metadata。
- SQLite 不能作为迁移正确性的唯一验证环境。

## CLI 命令

必须提供：

```text
core migrate plan
core migrate preflight
core migrate dry-run
core migrate apply
core migrate status
core migrate drift-check
```

所有命令支持：

```text
--json
--dry-run
--yes
```
