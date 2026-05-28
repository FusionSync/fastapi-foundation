# Platform App: Accounts

## 职责

Accounts 负责用户、登录、本地账号、外部身份绑定和当前用户资料。

## 与 core 的关系

- 使用 `core.auth` 提供的认证抽象。
- 不实现通用权限引擎。
- 不直接处理具体业务资源权限。

## 核心模型

```text
User
  id
  email
  display_name
  status
  auth_provider
  external_id
  token_version

UserCredential
  user_id
  password_hash
  password_updated_at

ExternalIdentity
  user_id
  provider
  subject

UserSession
  id
  user_id
  tenant_id
  auth_provider
  status
  token_version
  revoke_reason
  revoked_at
  created_at
```

## API

```text
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/me
PATCH /api/v1/me
```

## 迭代

第一版可以只支持本地 JWT；后续接入 Logto/Keycloak 后，Accounts 负责本地用户与外部 subject 的映射。

## 当前实现

第一版落点：

- `platform_apps.accounts.models.User` 保存本地用户基础资料和 `token_version`。
- `UserCredential` 和 `ExternalIdentity` 预留本地密码与 OIDC/Logto/Keycloak subject 映射。
- `UserSession` 保存 session_id、tenant_id、auth_provider、status 和创建时的 token_version。
- `AccountsService.create_session()` 只允许 active user 创建 session。
- `AccountsService.disable_user()` 会把 user 标记为 disabled、递增 token_version，并撤销该用户所有 active sessions。
- `AccountsService.revoke_tenant_sessions()` 可作为 `TenantLifecycleService` 的 `session_revocation_hook`，在租户暂停/删除时撤销对应 tenant 的 active sessions。

当前实现只负责会话事实和撤销收敛，不负责 JWT 签发。后续本地 JWT provider 应把 `session_id` 和 `token_version` 写入 token，并在请求认证时校验 session 是否仍 active。
