# Platform App: Tenants

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
  role
  status

TenantInvitation
  id
  tenant_id
  email
  role
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

Tenants 负责成员和角色事实，Permissions 负责授权决策。角色变更后同步到 Casbin policy。
