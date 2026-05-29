# Platform App: Accounts

## Progress

- Status: `connected`
- Done: local user/password credential、session fact、auth session store、permissions、session.created 强一致审计、账号安全事件 outbox、local login/logout/token refresh HTTP API、当前用户资料、password reset、外部身份绑定、session 管理 API、失败登录审计、account create 幂等 mutation guard checkpoint 和基础 account integration tests 已落地。
- Next:
  - _none_

## 职责

Accounts 负责用户、登录、本地账号、外部身份绑定和当前用户资料。

## 与 core 的关系

- 使用 `core.auth` 提供的认证抽象。
- 不实现通用权限引擎。
- 不直接处理具体业务资源权限。
- 通过 `platform_apps.accounts.module` 暴露 `AppModule`，注册模型、权限、迁移包、router、public_api 和 `auth_session_store`。

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
POST /api/v1/auth/refresh
GET  /api/v1/me
PATCH /api/v1/me
PATCH /api/v1/me/password
GET  /api/v1/me/external-identities
POST /api/v1/me/external-identities
GET  /api/v1/me/sessions
DELETE /api/v1/me/sessions/{session_id}
POST /api/v1/platform/accounts/users
PATCH /api/v1/platform/accounts/users/{user_id}/disable
POST /api/v1/platform/accounts/users/{user_id}/sessions/revoke
```

## 迭代

第一版可以只支持本地 JWT；后续接入 Logto/Keycloak 后，Accounts 负责本地用户与外部 subject 的映射。

## 当前实现

第一版落点：

- `platform_apps.accounts.models.User` 保存本地用户基础资料和 `token_version`。
- `UserCredential` 和 `ExternalIdentity` 预留本地密码与 OIDC/Logto/Keycloak subject 映射。
- `UserSession` 保存 session_id、tenant_id、auth_provider、status 和创建时的 token_version。
- `AccountsService.create_session()` 只允许 active user 创建 session；如果 session 绑定 tenant，必须先验证 Tenant 存在、用户是 active member，并通过 tenant lifecycle 的 `login` gate。
- `AccountsService.create_session()` 可注入 `AuditService` 写 `session.created` 强一致审计，记录 session、tenant、auth_provider、request_id 和 token_version。
- `AccountsService.create_session()` 可注入 `EventPublisher` 发布 `account.session_created` security outbox event。
- `AccountsService.create_local_user()` 创建 local user 并写 `UserCredential.password_hash`。
- `AccountsService.verify_local_password()` 使用 `core.security.PasswordHasher` 校验本地密码。
- `AccountsService.authenticate_local_login()` 串联本地密码校验和 session 创建；失败时写 `account.login_failed` 审计并发布 `account.login_failed` outbox event。
- `AccountsService.refresh_session_token()` 校验 `TokenClaims` 与 `UserSession/User` fact 一致后返回可重新签发本地 JWT 的 claims，并写 `session.refreshed` 审计和 `account.session_refreshed` outbox event。
- `platform_apps.accounts.router` 提供 local login/logout/refresh、`/me` 当前用户资料、密码重置、外部身份绑定和 session 管理 API；route 层签发本地 JWT，service 层仍只维护会话事实和安全事件。
- `AccountsService.update_profile()`、`reset_local_password()`、`bind_external_identity()`、`list_user_sessions()` 和 `revoke_own_session()` 支撑用户自助资料和 session 管理。
- `AccountsService.disable_user()` 需要 platform scope 的 `user.manage` / `user.disable` `AuthorizationDecision`；通过后会把 user 标记为 disabled、递增 token_version，并撤销该用户所有 active sessions。
- `AccountsService.disable_user()` 可注入 `AuditService` 写 `user.disabled` 强一致审计，记录撤销 session 数和新的 token_version。
- `AccountsService.disable_user()`、`revoke_user_sessions()` 和 `revoke_tenant_sessions()` 可发布 `account.user_disabled` / `account.session_revoked` security outbox event。
- `AccountsService.revoke_user_sessions()` 和 `AccountsService.revoke_tenant_sessions()` 需要 platform scope 的 `session.revoke` / `session.manage` `AuthorizationDecision`。
- `AccountsService.revoke_tenant_sessions_for_lifecycle()` 可作为 `TenantLifecycleService` 的内部 `session_revocation_hook`，在租户暂停/删除已经通过 lifecycle 授权后撤销对应 tenant 的 active sessions。
- `AccountsAuthSessionStore` 适配 `core.auth.AuthSessionValidator`，把 UserSession/User fact 转换为 core 统一认证主体。
- `platform_apps.accounts.module` 通过 `auth_session_store="platform_apps.accounts.public_api.AccountsAuthSessionStore"` 声明会话事实适配器，server runtime 可自动装配请求安全流水线。
- `platform_apps.accounts.module` 声明 `account.session_created`、`account.session_refreshed`、`account.login_failed`、`account.session_revoked` 和 `account.user_disabled` event schema。
- `platform_apps.accounts.permissions.PERMISSIONS` 注册 `user.manage` 和 `session.revoke` 平台权限。

当前实现只负责本地密码凭据、会话事实和撤销收敛，不直接签发 JWT。`core.auth.LocalJwtProvider` 可基于 `UserSession.id`、`UserSession.token_version` 和 `User.token_version` 签发/校验本地 token；请求认证时仍必须调用 `AuthSessionValidator`，确保禁用用户、撤销 session 和租户生命周期变更能收敛到访问控制。
