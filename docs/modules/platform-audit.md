# Platform App: Audit

## Progress

- Status: `partial`
- Done: audit model、AuditService、result/reason/session/policy fields、request/trace/route/method 默认 context 字段、hash chain、进程内链路锁、可选分布式链路锁、service/route 权限拒绝审计、账号 session 创建/撤销审计、tenant lifecycle 审计、WORM/SIEM NDJSON export、导出批次记录和 checksum 已落地。
- Done: platform audit HTTP APIs expose log query, hash-chain verification, export execution, and export record query.
- Done: audit APIs are protected with `audit_log.read` / `audit_log.export` platform permissions.
- Done: API checkpoint tests cover log query, hash-chain verification, WORM export, and export record retrieval.
- Done: log query supports actor/request/trace and created-at range filters.
- Done: retention API supports dry-run and execution; unsafe partial hash-chain deletion is rejected.
- Done: local SIEM export sink writes `.siem.jsonl` NDJSON objects for collector handoff.
- Next: add production WORM/SIEM provider adapters and profile-configured retention schedules.

## 职责

Audit 负责记录关键操作、授权失败、文件访问、任务执行和业务资源变更。
它通过 `platform_apps.audit.module` 暴露 `AppModule`，统一注册模型、权限、迁移包、router 和 public_api。

## 核心模型

```text
AuditLog
  id
  tenant_id
  actor_id
  actor_type
  auth_provider
  session_id
  action
  resource_type
  resource_id
  result
  reason
  policy_version
  request_id
  trace_id
  route
  method
  ip_address
  user_agent
  payload
  hash_prev
  hash
  created_at

AuditExportRecord
  id
  tenant_id
  actor_id
  destination_type
  destination_uri
  status
  request_id
  filters
  record_count
  hash_root
  hash_tip
  checksum_sha256
  error_message
  exported_at
  created_at
```

## 关键事件

- 登录成功/失败。
- 文件上传/下载/删除。
- 业务资源创建、修改、删除。
- 任务提交、成功、失败。
- 权限拒绝。
- 管理员配置变更。

## 设计要求

- 审计记录写入不能阻塞主流程太久。
- 生产环境审计日志不可随业务删除。
- 敏感字段必须脱敏。
- 私有化部署需要支持导出审计记录。
- 安全关键审计必须与业务或权限变更强一致写入，不能仅依赖 best-effort 异步事件。
- 生产 profile 应支持 hash chain 或外部 WORM/SIEM 适配，保证审计记录可追溯篡改。
- hash chain 写入必须按 `tenant_id` 串行化，避免并发事务读到同一个前驱 hash 后形成分叉。
- 审计保留、导出和删除策略必须按部署 profile 配置。

## 当前实现

第一版落点：

- `platform_apps.audit.models.AuditLog` 定义 append-oriented 审计表。
- `platform_apps.audit.services.AuditService.record()` 绑定调用方传入的 `AsyncSession`，不自行打开连接或提交事务。
- route/service 可通过同一个 unit-of-work 同时写业务数据和安全关键审计；业务事务 rollback 时审计同步 rollback。
- `AuditService` 会从 `RequestContext` 补齐 `tenant_id`、`actor_id`、`request_id`、`trace_id`、`route`、`method`、`ip_address`、`user_agent`。
- 入库前通过 `core.security.redact_sensitive_data()` 脱敏 password、token、secret、authorization 等字段。
- 每条记录写入 `hash_prev` 和 `hash`，hash chain 按 `tenant_id` 分区；平台级 `tenant_id=None` 记录使用独立链路，避免租户级导出或校验引用其他租户记录。
- `AuditService.record()` 对同一进程内的同一 tenant/platform 链路加锁，并持有到当前 SQLAlchemy session 外层事务结束，防止应用内并发写入形成 hash chain 分叉。
- private/cloud profile 可向 `AuditService` 注入 `LockProvider`，为每个 tenant/platform hash chain 获取 `audit:hash-chain:*` 分布式锁；锁占用时返回 `LOCK_NOT_ACQUIRED`，获取成功后同样持有到外层事务结束再释放。
- `AuditService.verify_hash_chain(tenant_id)` 可按租户校验本库内审计链路，发现 hash 不匹配、前驱缺失、分叉、多根和断链。
- `core.permissions.AuthorizationService` 会在权限拒绝时写入 `authorization.denied` 审计。
- `DatabaseRequestSecurityPipeline` 可通过 `audit_factory=AuditService` 持久化 route-level permission denied 审计；该审计记录会复用 `RequestContext` 中的 tenant、actor、request、IP 和 user agent 默认字段。
- `RoleGrantService` 可注入 `AuditService`，角色授予和撤销会写 `role.granted` / `role.revoked` 审计。
- `AccountsService` 可注入 `AuditService`，session 创建/撤销和禁用用户会写 `session.created` / `session.revoked` / `user.disabled` 审计。
- `TenantLifecycleService` 可注入 `AuditService`，租户创建、暂停、恢复、删除和归档会写对应 `tenant.*` 审计。
- `platform_apps.audit.permissions.PERMISSIONS` 注册 `audit_log.read` 和 `audit_log.export` 平台权限。
- `platform_apps.audit.router` 已暴露面向平台管理员的 HTTP 控制面：日志查询、hash chain 校验、导出执行和导出记录查询。
- 日志查询支持 `tenant_id`、`actor_id`、`action`、`resource_type`、`result`、`request_id`、`trace_id`、`created_from`、`created_to` 过滤。
- `AuditExportService.export_logs()` 在导出前校验目标 tenant/platform hash chain，失败时以 `CONFLICT` 拒绝并且不会写出导出对象。
- 导出格式为 `audit.ndjson.v1`：首行是 manifest，后续每行是一条审计记录，保留 `hash_prev`、`hash`、result、reason、session、policy 和 request/trace 字段，便于 SIEM 消费。
- `AuditExportRecord` 记录导出者、request_id、过滤条件、记录数、hash root/tip、目标 URI、状态、导出时间和 payload 的 `checksum_sha256`。
- `LocalWormAuditExportSink` 使用独占创建写入本地 `.jsonl` 对象，已存在同名导出时返回 `CONFLICT`，作为 WORM/object-storage adapter 的本地实现。
- `LocalSiemAuditExportSink` 使用 `.siem.jsonl` 后缀写出同一 NDJSON 格式，便于本地或私有化环境交给 SIEM collector 拉取。
- `POST /api/v1/platform/audit/retention` 会先按 `older_than` 计算匹配记录；`dry_run=true` 只返回匹配数量，`dry_run=false` 才执行删除。若删除会留下断开的 hash chain，接口返回 `CONFLICT`。

当前 hash chain 是数据库内轻量链路；多 worker 部署应使用 `DatabaseLockProvider`、后续 Redis/advisory lock provider 或同等分布式串行化能力。生产环境如果需要外部合规归档，可以通过 `AuditExportSink` protocol 接入对象存储 WORM bucket 或 SIEM collector。

## TODO

- [x] Add append-oriented `AuditLog`.
- [x] Add hash-chain verification service.
- [x] Add WORM/SIEM export service and export records.
- [x] Add `GET /api/v1/platform/audit/logs`.
- [x] Add `GET /api/v1/platform/audit/exports`.
- [x] Add `POST /api/v1/platform/audit/exports`.
- [x] Add `POST /api/v1/platform/audit/verify`.
- [x] Add route-level platform permissions for audit read/export APIs.
- [x] Add integration tests for audit log query, export record query, export execution, and chain verification.
- [x] Add actor/request/trace/time-range query filters.
- [x] Add local SIEM export sink.
- [x] Add retention dry-run/apply API with hash-chain safety check.
- [ ] Add production WORM/SIEM provider adapters.
