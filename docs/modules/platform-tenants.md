# Platform App: Tenants

## Progress

- Status: `partial`
- Done: tenant/member public app、module metadata、permissions 和与 core tenancy model 的复用关系已落地。
- Next:
  - [ ] 补 invitation flow。
  - [ ] 将成员接受邀请后的初始角色交给 RoleGrant/permission projection 处理。

## 职责

Tenants 负责组织、成员、邀请、角色绑定和租户配置。

## 核心模型

```text
Tenant
  id
  name
  code
  status
  deployment_mode

TenantMember
  id
  tenant_id
  user_id
  status

TenantInvitation
  id
  tenant_id
  email
  role_template_id
  token
  expires_at
```

## API

```text
GET  /api/v1/tenants
POST /api/v1/tenants
GET  /api/v1/tenants/{id}/members
POST /api/v1/tenants/{id}/members
PATCH /api/v1/tenants/{id}/members/{member_id}
```

## 权限

- 租户 owner 可以管理成员和配置。
- admin 可以邀请成员。
- viewer 只能读取自己所属租户信息。

## 与权限模块关系

Tenants 负责成员关系和成员状态，Permissions 负责 RoleGrant、授权决策和策略投影。邀请可以携带初始 `role_template_id`，用户接受邀请后由权限模块创建 RoleGrant。

## 当前实现

第一版 `platform_apps.tenants.module` 先作为 tenant lifecycle 的平台权限入口：

- 通过 `platform_apps.tenants.permissions.PERMISSIONS` 注册 `tenant.manage`、`tenant.provision`、`tenant.suspend`、`tenant.reactivate` 和 `tenant.delete`。
- 通过 `platform_apps.tenants.public_api` 暴露 `Tenant`、`TenantMember` 和 `TenantLifecycleService`。
- 模型事实仍复用 `core.tenancy.models`，避免在平台 app 中重复定义租户状态机表。
