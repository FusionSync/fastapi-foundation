# Core Permission Model

## 职责

Permission Model 定义权限数据的事实源、权限点、角色模板、角色授予和策略投影。Casbin 只是授权执行引擎，不应成为唯一事实源。

## 核心原则

```text
RoleGrant 是角色授予事实源
TenantMember 是成员关系事实源
Casbin policy 是投影
PermissionSpec 是权限目录
```

角色授予应先写 RoleGrant，再通过 Outbox 同步到 Casbin policy。角色撤销应删除 RoleGrant 事实，并通过同一个 Outbox event 清理对应投影。TenantMember 不再保存角色字段，只表达用户是否属于某租户以及成员状态。

## PermissionSpec

每个 app 在 module 中注册权限点时，应扩展为：

```text
resource
action
scope
description
risk_level
```

示例：

```text
resource = workspace
action = write
scope = tenant
risk_level = normal
```

## Scope

第一版支持：

```text
tenant
own
resource
platform
```

含义：

- `tenant`：租户内资源。
- `own`：本人创建或负责的资源。
- `resource`：具体资源实例授权。
- `platform`：平台级跨租户权限。

## 资源实例级授权

权限校验分两步：

```text
1. 校验资源是否属于当前 tenant
2. 校验当前用户是否有 action 权限
```

禁止只校验 `resource/action`，不校验资源实例归属。

## 角色模型

```text
RoleTemplate
  id
  scope
  name
  version
  permissions

RoleGrant
  id
  tenant_id
  subject_type
  subject_id
  role_template_id
  policy_version
```

内置角色：

```text
owner
admin
editor
viewer
```

平台角色和租户角色必须分离。

`is_platform_admin` 不能作为 `CurrentUser` 上的绕过开关。平台管理员必须来自 platform scope 的 `RoleGrant` 或外部身份 claim 到 RoleGrant 的可审计映射。第一版使用固定 platform domain `__platform__` 表达平台级授权事实。

## Policy 投影

角色或权限变更流程：

```text
写 RoleGrant 或 TenantMember
  -> 同事务写 outbox event
  -> policy projector 消费事件
  -> 更新 Casbin policy
  -> 更新 policy_version
  -> 失效权限缓存
```

必须提供 reconciliation job：

```text
facts -> expected policies -> actual policies -> diff -> repair
```

reconciliation repair 必须是增量修复：

- 缺失的 expected policy 只补 missing。
- 多余或过期的 actual policy 只删 stale。
- 相同 tenant/subject/resource/action/role_grant_id 下的 `policy_version` 或 `effect` 漂移必须被视为 stale + missing，而不是误判为一致。
- repair 必须失效权限缓存。

CLI 入口：

```bash
core permissions reconcile --database-url <sqlalchemy-async-url> --json
core permissions reconcile --database-url <sqlalchemy-async-url> --repair --json
```

不带 `--repair` 时只报告 missing/stale policy；带 `--repair` 时执行增量修复并提交事务。

当前实现落点：

```text
RoleTemplate / RoleGrant / ProjectedPolicy
  src/core/permissions/models.py

PermissionRegistry
  src/core/permissions/registry.py

PolicyProjector / reconciliation
  src/core/permissions/projector.py

AuthorizationService
  查询 ProjectedPolicy，并在拒绝时写 authorization.denied 审计

RoleGrantService
  授予时写 RoleGrant 事实，并在同一事务写 permissions.role_grant_changed outbox event
  撤销时删除 RoleGrant 事实，并在同一事务写 permissions.role_grant_changed outbox event
  撤销时同步删除该 grant 已有 ProjectedPolicy，避免 outbox projector 消费前旧投影继续授权
  授予和撤销都必须传入允许的 AuthorizationDecision，且 actor_id 必须与 decision.user_id 一致
  可注入 AuditService 写 role.granted / role.revoked 强一致审计
```

第一版的 `ProjectedPolicy` 是 Casbin policy 的可替换投影层。后续接入真实 Casbin adapter 时，事实源仍然是 `RoleGrant`，不能让业务代码直接写 Casbin policy。

## 审计

必须审计：

- 角色授予。
- 角色撤销。
- 权限模板修改。
- 跨租户权限使用。
- platform admin 操作。
