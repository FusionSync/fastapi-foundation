# Platform App: Audit

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
  ip_address
  user_agent
  payload
  hash_prev
  hash
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
- `AuditService` 会从 `RequestContext` 补齐 `tenant_id`、`actor_id`、`request_id`、`ip_address`、`user_agent`。
- 入库前通过 `core.security.redact_sensitive_data()` 脱敏 password、token、secret、authorization 等字段。
- 每条记录写入 `hash_prev` 和 `hash`，hash chain 按 `tenant_id` 分区；平台级 `tenant_id=None` 记录使用独立链路，避免租户级导出或校验引用其他租户记录。
- `AuditService.record()` 对同一进程内的同一 tenant/platform 链路加锁，并持有到当前 SQLAlchemy session 外层事务结束，防止应用内并发写入形成 hash chain 分叉。
- `AuditService.verify_hash_chain(tenant_id)` 可按租户校验本库内审计链路，发现 hash 不匹配、前驱缺失、分叉、多根和断链。
- `core.permissions.AuthorizationService` 会在权限拒绝时写入 `authorization.denied` 审计。
- `RoleGrantService` 可注入 `AuditService`，角色授予和撤销会写 `role.granted` / `role.revoked` 审计。
- `AccountsService` 可注入 `AuditService`，禁用用户会写 `user.disabled` 审计。
- `TenantLifecycleService` 可注入 `AuditService`，租户创建、暂停、恢复、删除和归档会写对应 `tenant.*` 审计。
- `platform_apps.audit.permissions.PERMISSIONS` 注册 `audit_log.read` 和 `audit_log.export` 平台权限。

当前 hash chain 是数据库内轻量链路，不替代外部 WORM 或 SIEM。进程内链路锁只覆盖单进程并发；private/cloud 多 worker 部署必须继续接数据库 advisory lock、Redis lock 或同等分布式串行化能力。生产环境如果有合规要求，应把审计导出和不可篡改存储作为部署 profile 能力继续接上。
