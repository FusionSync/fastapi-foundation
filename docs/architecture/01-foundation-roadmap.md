# Foundation Roadmap

## 目标

本路线图用于约束后续实现顺序：先补齐 P0/P1 的架构契约，再按“大功能”演进。每完成一个大功能后运行该功能的 checkpoint tests；不在每个小任务后频繁跑全量测试。

## P0 已纳入契约

- HTTP 默认使用标准 status，`always_200` 只作为兼容模式。
- 租户解析必须校验认证主体、membership 和 tenant lifecycle status。
- tenant-scoped 数据访问必须走 `TenantScopedRepository`、`TenantScopedQuery` 或 raw SQL wrapper。
- outbox 保持轻量：同事务落表、条件领取、有限重试、死信重放、handler 以 `event_id` 幂等。
- `AppModule` 是 app 接入、迁移、权限、事件、任务和调度的单一注册事实源。
- 权限事实源收敛：TenantMember 表达成员关系，RoleGrant 表达角色授予。
- 生产运行拆分 server、worker、scheduler、outbox-dispatcher、migrate。
- 备份恢复和发布顺序进入 operations 文档。
- ORM 基线统一为 SQLAlchemy 2.x async + Alembic。

## P1 已纳入契约

- response envelope、错误码 registry、headers 和 OpenAPI contract 统一治理。
- ContextVar 使用冻结 RequestContext 和 try/finally reset。
- serialization golden rules 明确 datetime、Decimal、UUID、Enum 和 null envelope 字段。
- idempotency 采用持久状态机和原子 insert-and-claim。
- locks 只做并发保护，不替代持久幂等和唯一约束。
- observability 定义 `/metrics`、trace_id、app_code、service_role、instance_id。
- config 定义 profile 校验、secret provider 接口和脱敏诊断。
- audit 增加 result、reason、session_id、policy_version、hash chain/WORM 预留。

## 大功能演进顺序

### 1. Core Runtime Foundation

范围：

- pyproject/tooling。
- settings/config。
- app factory。
- context middleware。
- response envelope。
- exception/code registry。
- health/metrics skeleton。

Checkpoint：

- core app can start without business apps。
- response envelope contract tests。
- config/security startup checks。

### 2. App Module And Golden App

范围：

- typed `AppModule`。
- app registry/loader。
- app dependency graph。
- import/public_api lint。
- example app。
- `core check-app`、`core list-apps`。

Checkpoint：

- golden app registers without manual imports。
- app conformance tests pass。

### 3. Data Foundation And Tenant Isolation

范围：

- SQLAlchemy async engine/session。
- base models/schemas/repositories。
- unit-of-work。
- tenant resolver。
- tenant repository/query guard。
- raw SQL wrappers。

Checkpoint：

- tenant A cannot read/write tenant B data through default paths。
- raw SQL without tenant scope is rejected。

### 4. Lightweight Outbox And Events

范围：

- `OutboxEvent` model。
- outbox repository。
- dispatcher。
- handler registry。
- dead-letter replay CLI。

Checkpoint：

- committed business write leaves outbox event。
- rollback leaves no event。
- dispatcher retry does not duplicate side effects when handler uses `event_id` idempotency。

### 5. Migration Governance

范围：

- Alembic integration。
- migration manifest。
- planner/preflight/dry-run/apply/status/drift-check。
- expand-contract guidance and gates。

Checkpoint：

- destructive migration blocked without classification and approval。
- drift check detects mismatch。

### 6. Platform Apps Foundation

范围：

- accounts/users/sessions。
- tenants/members/lifecycle。
- files metadata/storage provider。
- audit logging。

Checkpoint：

- tenant lifecycle matrix enforced for login/read/write/task/file。
- security-critical audit written strongly.

### 7. Permission Facts And Projection

范围：

- PermissionSpec registry。
- RoleTemplate/RoleGrant。
- Casbin projector。
- policy cache invalidation。
- reconciliation command。

Checkpoint：

- role grant changes update authorization result。
- reconciliation detects and repairs projection drift。

### 8. Tasks, Scheduler, Operations

范围：

- task provider abstraction。
- scheduler provider abstraction。
- process commands。
- deployment smoke checks。
- backup-readiness checks。

Checkpoint：

- local profile runs server/worker/scheduler/outbox-dispatcher。
- operations smoke checks pass。

## 测试节奏

- 小任务期间只做必要 smoke check。
- 一个大功能完成后运行对应 checkpoint suite。
- 多个大功能串起来后再运行完整 contract/integration/unit suite。

## Progress 记录规则

- 每个 `docs/modules/*.md` 必须维护 `## Progress`，记录当前 Status、已完成事实和下一步 TODO。
- Progress 的 TODO 只能沿着本路线图的大功能 checkpoint 前进；模块级优化放在相关大功能连接完成之后。
- 完成一个大功能时，同步更新所有受影响模块的 Progress，再运行对应 checkpoint suite。
