# Platform App: Tenants

## Progress

- Status: `connected`
- Done: tenant/member public app、module metadata、permissions、invitation flow、接受邀请后的初始 RoleGrant/projection outbox 链路、租户/成员/invitation HTTP route protection、真实 HTTP handler service wiring 和与 core tenancy model 的复用关系已落地。
- Next:
  - _none_

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
  token_hash
  status
  expires_at
```

## API

```text
GET   /api/v1/platform/tenants
POST  /api/v1/platform/tenants
GET   /api/v1/tenants/{tenant_id}/members
POST  /api/v1/tenants/{tenant_id}/members
PATCH /api/v1/tenants/{tenant_id}/members/{member_id}
POST  /api/v1/tenants/{tenant_id}/invitations
PATCH /api/v1/tenants/{tenant_id}/invitations/{invitation_id}/revoke
POST  /api/v1/tenant-invitations/accept
```

当前 API routes 已接入真实 service：平台租户创建使用 `TenantLifecycleService`，成员增改查使用 `TenantMembershipService`，邀请签发/撤销/接受使用 `TenantInvitationService`，并继续通过 route permission dependency 向 service 层传递授权证明。

## 权限

- 租户 owner 可以管理成员和配置。
- admin 可以邀请成员。
- viewer 只能读取自己所属租户信息。

## 与权限模块关系

Tenants 负责成员关系和成员状态，Permissions 负责 RoleGrant、授权决策和策略投影。邀请可以携带初始 `role_template_id`，用户接受邀请后由权限模块创建 RoleGrant。
创建带初始角色的邀请时必须同时提供 `tenant_invitation:invite` 和 `role_grant:grant` 授权决策；接受邀请后由 `TenantInvitationService` 激活 `TenantMember`，再通过 `RoleGrantService` 写入 `RoleGrant` 并发布 `permissions.role_grant_changed`，由 permission projector 生成 `ProjectedPolicy`。

## 当前实现

第一版 `platform_apps.tenants.module` 先提供 tenant lifecycle、membership 和 invitation 的平台能力入口：

- 通过 `platform_apps.tenants.permissions.PERMISSIONS` 注册 `tenant.manage`、`tenant.provision`、`tenant.suspend`、`tenant.reactivate`、`tenant.delete`、`tenant_member:read`、`tenant_member:manage`、`tenant_invitation:invite`、`tenant_invitation:revoke` 和 `tenant_invitation:manage`。
- `platform_apps.tenants.router` 声明 platform scope 的 `/platform/tenants` route，以及 tenant scope 的成员和 invitation route；所有 route 均返回统一 envelope，并由 request security pipeline 先完成认证、租户上下文和权限校验。
- 通过 `platform_apps.tenants.public_api` 暴露 `Tenant`、`TenantMember`、`TenantInvitation`、`TenantLifecycleService`、`TenantMembershipService`、`TenantQueryService` 和 `TenantInvitationService`。
- `TenantInvitation` 只持久化 `token_hash`，明文 token 只在 `TenantInvitationIssue` 中返回一次。
- `tenant.created/suspended/reactivated/deleting/archived/deleted`、`tenant.member_activated`、`tenant.invitation_issued`、`tenant.invitation_accepted` 和 `tenant.invitation_revoked` 已声明为 app event schema。
- 模型事实仍复用 `core.tenancy.models`，避免在平台 app 中重复定义租户状态机表。
