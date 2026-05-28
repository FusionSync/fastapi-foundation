# Platform App: Tenant Lifecycle

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
  正常使用。

suspended
  暂停服务，通常因为欠费、合规、管理员操作。

deleting
  进入删除流程，冻结写入，等待异步清理。

archived
  业务数据归档，不再允许正常访问。

deleted
  可删除数据已清理，仅保留合规要求的审计记录。
```

## 状态行为矩阵

```text
状态          登录  读取  写入  任务  文件下载  管理操作
provisioning  否    否    否    否    否        是
active        是    是    是    是    是        是
suspended     是    是    否    否    可配置    是
deleting      否    否    否    否    否        是
archived      否    可配置 否    否    可配置    是
deleted       否    否    否    否    否        平台只读
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

TenantLifecycleService
  provision_tenant()
  suspend_tenant()
  begin_delete_tenant()
  finish_delete_tenant()
```

暂停和删除流程会调用 session revocation hook，并写入租户生命周期 outbox event。`TenantLifecycleService` 可注入 `AuditService`，在同一事务写租户状态流转审计，记录 from/to 状态、事件类型和是否撤销 session。API 层、任务执行、文件下载和后台清理应统一调用 lifecycle gate，而不是各自判断 `status` 字段。
