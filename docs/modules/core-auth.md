# Core Auth

## Progress

- Status: `connected`
- Done: `CurrentUser`、local JWT provider、session validator、token refresh claims 校验、local auth HTTP login/logout/refresh、request security pipeline、auth-only route 认证、platform scope route 授权、route authorization decision 传递、app 声明式 `auth_session_store`、OIDC provider adapter、Logto/Keycloak provider config helper、外部 provider state/nonce/callback contract 和外部 provider callback 登录链路已接入。
- Next:
  - _none_

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
- `OidcProviderAdapter` 提供外部 provider 授权 URL 生成和 callback 处理契约；`MemoryExternalAuthStateStore` 负责一次性 state/nonce，callback 会校验 state、nonce、issuer、audience、签名和过期时间。
- `logto_oidc_provider_config()` 和 `keycloak_oidc_provider_config()` 固化 Logto/Keycloak 的 provider label、授权端点、token 端点、scope 和 tenant claim 默认值。
- `OidcClient` 与 `OidcIdTokenVerifier` 是外部 token exchange 和 ID token 验签协议；内置 `HmacOidcIdTokenVerifier` 覆盖本地/对称签名场景，生产 Logto/Keycloak 可注入 JWKS/RS256 等价 verifier，而 accounts callback route 不直接绑定具体厂商 SDK。
- 多副本部署时应把 `app.state.external_auth_state_store` 替换为 Redis/数据库等分布式一次性 state store，避免 callback 落到不同进程后找不到 state。
- `platform_apps.accounts.AccountsAuthSessionStore` 是当前 SQLAlchemy 适配器，读取 `UserSession` 和 `User`。
- `platform_apps.accounts.AccountsService.refresh_session_token()` 复用 `TokenClaims` 和 `UserSession/User` fact 校验，route 层可用返回 claims 调用 `LocalJwtProvider.issue_token()` 重新签发本地 JWT。
- `platform_apps.accounts` 已提供 local login/logout/refresh HTTP API 和 `/auth/external/{provider}/authorize`、`/auth/external/{provider}/callback` 外部 provider callback API；route 层负责把外部身份映射为本地 session 后签发本地 access token，core auth 继续只提供 provider、claims 和 session validation 契约。
- `DatabaseRequestSecurityPipeline` 串联 HTTP Bearer token、`AuthSessionValidator`、`DatabaseTenantContextResolver` 和 route permission authorization。`tenant_required=False` 且没有 tenant 权限的 route 只绑定认证用户，不强制租户上下文；`permission_scope="platform"` 的 route 通过 `AuthorizationService.require_platform()` 使用 platform domain 授权事实。权限校验通过后返回 `AuthorizationDecision`，由 router dependency 写入当前 request，供业务 mutation 继续传给 service 层。安装声明了 `AppModule.auth_session_store` 的账号 app 时，`create_app()` 会自动装配该 pipeline；也可通过 `create_app(..., request_security_pipeline=...)` 显式覆盖。

当前先由 `platform_apps.accounts` 落地会话事实：

- `User.token_version` 表达用户级 token 撤销版本。
- `UserSession.status` 表达 session 是否 active/revoked。
- 禁用用户会递增 token_version 并撤销 active sessions。
- 租户生命周期服务可通过 accounts 的 session revocation hook 撤销指定 tenant sessions。
- `AccountsService.create_local_user()` 使用 `core.security.PasswordHasher` 创建本地密码凭据。
- `AccountsService.verify_local_password()` 校验本地密码，失败时抛 `AUTH_INVALID_TOKEN`。
- `AccountsService.authenticate_local_login()` 会把失败登录写入审计和 `account.login_failed` outbox；`create_session()`、`refresh_session_token()` 和撤销/禁用路径可发布账号安全事件 outbox。

请求认证流程应先由 token provider 完成 token 层校验，再把 claims 交给 `AuthSessionValidator`：

```text
LocalJwtProvider.verify_token() 或 OIDC callback 后签发的本地 access token
  -> TokenClaims
token.session_id 对应 UserSession.status == active
token.token_version == User.token_version
User.status == active
DatabaseTenantContextResolver 校验 Tenant/TenantMember
RouteSecurityPolicy.permissions 通过 AuthorizationService.require() 或 require_platform() 校验 ProjectedPolicy
  -> AuthorizationDecision 写入 request state
```

这样本地 JWT、OIDC/Logto 或 Keycloak callback 登录都复用同一套撤销事实；外部 ID token 只用于 callback 换取本地 session，不绕过 `AuthSessionValidator`。
