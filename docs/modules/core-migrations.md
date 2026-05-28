# Core Migrations

## Progress

- Status: `connected`
- Done: migration manifest、registry、planner、preflight、drift check、CLI plan/apply/status/run 契约、显式 Alembic executor apply 路径和 release checkpoint migrate dry-run stage 已落地；默认未配置 executor 时仍保持 metadata-disabled。
- Next:
  - [ ] 接跨进程 migration lock provider。
  - [ ] 细化 expand/backfill/contract 分阶段 apply 命令和回滚/forward-fix 记录。

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
alembic_revision
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
- `alembic_revision`：必须绑定真实 Alembic revision id，后续 runner 只能执行已进入 manifest 的 revision。
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
core migrate run
```

所有命令支持：

```text
--json
--dry-run
--yes
```

## 当前实现

已落地迁移 metadata 治理闭环：

- `MigrationManifest` 定义 app、migration_id、phase、classification、依赖、行数、锁风险、backfill、rollback、审批和 destructive operations。
- `MigrationManifest.validate()` 要求每条 migration 显式声明 `alembic_revision`，并在 CLI JSON 中输出该绑定。
- `MigrationRegistry.from_app_registry()` 从 `AppModule.migrations.path` 收集 `manifest.py` 中的 `MIGRATIONS`。
- app conformance 会在启动前读取 `manifest.py`，拒绝非 `MigrationManifest` 条目、app_label 不匹配、重复 key 和 `MigrationManifest.validate()` 返回的字段级错误，并把错误定位到具体 manifest module。
- `plan_migrations()` 按 migration 依赖和 app dependency graph 输出顺序计划。
- `run_preflight()` 校验 manifest、schema drift、destructive/backup readiness、high lock risk 和 forward-only warning。
- `core migrate dry-run` 已输出 `MigrationApplyResult` 兼容结构：`applied=false`、`mode=metadata-dry-run`，并复用 preflight gate。
- `core migrate apply` 已接入门禁：必须传 `--yes`，并在 apply 前运行 preflight；destructive 或 `requires_backup_restore` migration 必须额外传 `--backup-ready`。
- 未传 `--alembic-config` 时，`apply` 仍返回 `ok=false`、`applied=false`、`mode=metadata-apply-disabled`，避免 CI/CD 把 metadata/no-op 当作已应用。
- `apply_migrations()` 提供真实 executor 的受控执行契约：调用方必须注入 `MigrationExecutor` 和 `LockProvider`，执行前获取 `migrations:apply` 锁，执行后释放锁。
- executor 只能执行 preflight plan 中声明的 `alembic_revision`；runner 会校验 executor 返回的 `applied_revisions` 与计划完全一致，否则返回 `ok=false`、`applied=false`、`migration executor revision mismatch`。
- `AlembicMigrationExecutor` 使用显式 Alembic config 执行 manifest 绑定的 revision，并在每个 revision 后读取数据库当前 heads 验证已到达目标 revision。
- `core migrate apply --alembic-config <path> --database-url <url> --yes --json` 会通过真实 executor 执行；CLI 当前使用进程内 lock provider，后续 private/cloud profile 必须替换为跨进程 lock provider。
- `core migrate run --json` 是 migrate 进程角色入口；默认执行 `plan -> preflight -> dry-run` 并输出阶段化 envelope，传 `--apply --yes` 时复用 `migrate apply` 的 preflight、approval 和 executor gate。

后续扩展真实 runner 时，必须复用同一个 `MigrationApplyResult` 输出结构和 preflight gate，不能绕过 manifest 治理；只有真实执行数据库变更并验证 revision 状态后才能返回 `applied=true`。dry-run 只允许验证将执行的 revision，不允许改变数据库状态。
