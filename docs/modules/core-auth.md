# Core Auth

## Progress

- Status: `partial`
- Done: `CurrentUser`、local JWT provider、session validator、request security pipeline、route authorization decision 传递和 app 声明式 `auth_session_store` 已接入。
- Next:
  - [ ] 接 Logto/Keycloak 等外部 provider adapter。
  - [ ] 补 token refresh、session revocation API 和安全审计联动。

## 职责

Auth 模块负责认证抽象、当前用户解析、token 校验和认证 provider 适配。

## 不负责

- 不直接定义业务权限。
- 不直接决定用户是否能操作某个业务资源。
- 不绑定具体厂商认证系统。

## Provider 设计

```text
local_jwt
  本地账号密码，适合 MVP、本地单机版和演示。

logto
  SaaS/B2B 场景，支持组织、多租户和 OIDC。

keycloak
  私有化和政企场景，支持 LDAP/AD/SSO。
```

## 目录建议

```text
src/core/auth/
  provider.py
  deps.py
  jwt_provider.py
  oidc_provider.py
  schemas.py
```

## 当前用户对象

core 应提供统一 `CurrentUser`：

```text
id
external_id
email
display_name
auth_provider
session_id
token_version
```

业务 app 只依赖 `CurrentUser`，不关心 token 来自本地 JWT、Logto 还是 Keycloak。

`CurrentUser` 不包含 `is_platform_admin` 这类绕过字段。平台管理员身份通过权限系统查询 platform scope grant 得出，并进入审计。

请求解析租户时，`core.tenancy.DatabaseTenantContextResolver` 接收 `CurrentUser`，再从数据库读取 `Tenant` 和 `TenantMember` 事实；业务代码不应自己构造 tenancy membership 列表。

## 与账号 app 的关系

`platform_apps.accounts` 负责用户表、登录接口、账号绑定和成员关系。`core.auth` 只提供认证抽象和依赖。

## 安全要求

- 生产环境 JWT secret 不允许使用默认值。
- OIDC 必须校验 issuer、audience、签名和过期时间。
- 所有写接口必须依赖认证。
- 必须支持 `session_id`、`jti` 或 `token_version` 撤销机制，用于用户禁用、租户暂停/删除、角色撤销后的访问收敛。
- token refresh 和会话管理可以分阶段实现，但撤销检查接口必须在 core auth 契约中预留。

## 当前实现

当前已落地 core 侧认证主体和 session 撤销校验契约：

- `TokenClaims` 表达 token 解析后的最小 claims：`user_id`、`session_id`、`auth_provider`、`token_version`、`tenant_id`。
- `CurrentUser` 是业务 app 可依赖的统一当前用户对象，不包含平台管理员绕过字段。
- `AuthSessionValidator` 通过 `AuthSessionStore` 协议加载 session/user fact，统一校验 session 是否 active、user 是否 active、token_version 是否匹配、tenant 是否匹配。
- `StaticAuthSessionStore` 用于测试和本地 contract。
- 认证失败统一抛 `AUTH_INVALID_TOKEN`，并带 `WWW-Authenticate: Bearer`。
- `LocalJwtProvider` 提供本地 HS256 JWT 签发和校验，校验签名、issuer、audience 和过期时间，并把 `session_id`、`token_version`、`tenant_id` 转换为统一 `TokenClaims`。
- `platform_apps.accounts.AccountsAuthSessionStore` 是当前 SQLAlchemy 适配器，读取 `UserSession` 和 `User`。
- `DatabaseRequestSecurityPipeline` 串联 HTTP Bearer token、`AuthSessionValidator`、`DatabaseTenantContextResolver` 和 route permission authorization。权限校验通过后返回 `AuthorizationDecision`，由 router dependency 写入当前 request，供业务 mutation 继续传给 service 层。安装声明了 `AppModule.auth_session_store` 的账号 app 时，`create_app()` 会自动装配该 pipeline；也可通过 `create_app(..., request_security_pipeline=...)` 显式覆盖。

当前先由 `platform_apps.accounts` 落地会话事实：

- `User.token_version` 表达用户级 token 撤销版本。
- `UserSession.status` 表达 session 是否 active/revoked。
- 禁用用户会递增 token_version 并撤销 active sessions。
- 租户生命周期服务可通过 accounts 的 session revocation hook 撤销指定 tenant sessions。
- `AccountsService.create_local_user()` 使用 `core.security.PasswordHasher` 创建本地密码凭据。
- `AccountsService.verify_local_password()` 校验本地密码，失败时抛 `AUTH_INVALID_TOKEN`。

请求认证流程应先由 token provider 完成 token 层校验，再把 claims 交给 `AuthSessionValidator`：

```text
LocalJwtProvider.verify_token()
  -> TokenClaims
token.session_id 对应 UserSession.status == active
token.token_version == User.token_version
User.status == active
DatabaseTenantContextResolver 校验 Tenant/TenantMember
RouteSecurityPolicy.permissions 通过 AuthorizationService.require() 校验 ProjectedPolicy
  -> AuthorizationDecision 写入 request state
```

这样本地 JWT、后续 OIDC/Logto 或 Keycloak 适配都能复用同一套撤销事实。
