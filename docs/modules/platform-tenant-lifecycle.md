# Platform App: Tenant Lifecycle

## Progress

- Status: `connected`
- Done: tenant lifecycle 状态机、provision/suspend/reactivate/delete/archive service、删除/归档编排器、任务取消、业务/文件清理 hook、forward-fix/retry step record、archived/suspended 可配置策略、ready/health 运维诊断、outbox event、session revocation hook、audit hook、权限 decision 校验、login/read/write/task/file download/background cleanup gate、文件对象 retention cleanup 和端到端 checkpoint suite 已落地。
- Next: _none_

## 职责

Tenant Lifecycle 定义租户从创建、启用、暂停到删除/归档的状态机。多租户底座不能只有一个模糊的 `status` 字段。

## 状态机

```text
provisioning
  -> active
  -> suspended
  -> deleting
  -> archived
  -> deleted
```

## 状态含义

```text
provisioning
  正在初始化租户、默认角色、默认配置、存储目录。

active
  正常使用，可因运营或合规原因暂停。

suspended
  暂停服务，通常因为欠费、合规、管理员操作；问题解除后可恢复 active。

deleting
  进入删除流程，冻结写入，等待异步清理。

archived
  业务数据归档，不再允许正常访问。

deleted
  可删除数据已清理，仅保留合规要求的审计记录。
```

## 状态行为矩阵

```text
状态          登录  读取  写入  任务  文件下载  后台清理  管理操作
provisioning  否    否    否    否    否        否        是
active        是    是    是    是    是        是        是
suspended     是    是    否    否    可配置    否        是
deleting      否    否    否    否    否        是        是
archived      否    可配置 否    否    可配置    否        是
deleted       否    否    否    否    否        否        平台只读
```

## 删除流程

删除必须异步执行：

```text
1. 标记 tenant 为 deleting
2. 冻结写入
3. 撤销 session/token
4. 停止或取消后台任务
5. 导出或归档必要数据
6. 清理业务数据
7. 清理文件对象
8. 保留合规审计
9. 标记 archived 或 deleted
```

## 不变量

- 禁止删除最后一个 owner。
- owner 转移必须在事务中完成。
- suspended/deleting 租户不能创建新业务资源。
- 删除流程必须可重试。
- 每一步必须写审计。

## 初始化流程

租户创建时需要：

- 创建 Tenant。
- 创建 owner membership。
- 初始化默认角色模板。
- 初始化默认权限策略。
- 初始化存储命名空间。
- 初始化租户配置。
- 写入 `tenant.created` outbox event。

当前实现落点：

```text
Tenant / TenantMember
  src/core/tenancy/models.py

TenantLifecyclePolicy / behavior matrix / transition validation
  src/core/tenancy/lifecycle.py

TenantLifecycleService / TenantDeletionOrchestrator
  provision_tenant()
  suspend_tenant()
  reactivate_tenant()
  begin_delete_tenant()
  finish_delete_tenant()
  run()
```

创建、暂停、恢复、删除和归档流程都会写入租户生命周期 outbox event。暂停和删除流程会调用 session revocation hook。`TenantLifecycleService` 可注入 `AuditService`，在同一事务写租户状态流转审计，记录 from/to 状态、事件类型和是否撤销 session。API 层、账号登录、任务执行、文件下载和后台清理统一调用 lifecycle gate，而不是各自判断 `status` 字段；checkpoint suite 覆盖 login/read/write/task/file download/background cleanup 的关键矩阵。文件对象 retention cleanup 已通过 `FileService.purge_deleted_files()` 接入该 gate，`deleting` 状态允许后台清理继续推进租户删除流程。

`TenantDeletionOrchestrator` 负责把删除/归档串成可重试步骤：先进入 `deleting`，再取消该 tenant 下未完成的 `TaskRun`，调用业务数据清理 hook，调用文件清理 hook，最后标记 `archived` 或 `deleted`。每个步骤会写入 `TenantLifecycleStepRecord`，记录 `attempt_count`、`result_payload`、`last_error` 和 `forward_fix_required`；失败步骤保留记录并返回 `TENANT_DELETE_STEP_FAILED`，下一轮从失败步骤继续重试，已成功步骤不重复执行。

`TenantLifecyclePolicy` 可由 `Settings.tenant_lifecycle` 生成，用于控制 `suspended` 租户是否允许文件下载、`archived` 租户是否允许读取和文件下载。`DatabaseRequestSecurityPipeline` 会把该策略传给租户解析 gate，`FileService` 也可注入同一策略，确保 route/read 和 file download 不分叉。`/readyz` 和 `core <role> --json` 的 process health 会输出当前 `tenant_lifecycle_policy`，profile template/drift-check 也会检查 `TENANT_LIFECYCLE__*` 环境变量。

Tenant lifecycle mutation 是高权限写操作，不能只依赖 `actor_id`。`provision_tenant()`、`suspend_tenant()`、`reactivate_tenant()`、`begin_delete_tenant()` 和 `finish_delete_tenant()` 都需要 platform scope 的 `tenant.manage` / 对应 mutation `AuthorizationDecision`。service 会校验 decision 已允许、actor 与 decision user 一致、tenant domain 为 `__platform__`，并且 resource/action 覆盖当前操作。

`platform_apps.tenants.module` 注册 tenant lifecycle 相关权限点：

- `tenant.manage`
- `tenant.provision`
- `tenant.suspend`
- `tenant.reactivate`
- `tenant.delete`
