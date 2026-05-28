# Core Auth

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

## 与账号 app 的关系

`platform_apps.accounts` 负责用户表、登录接口、账号绑定和成员关系。`core.auth` 只提供认证抽象和依赖。

## 安全要求

- 生产环境 JWT secret 不允许使用默认值。
- OIDC 必须校验 issuer、audience、签名和过期时间。
- 所有写接口必须依赖认证。
- 必须支持 `session_id`、`jti` 或 `token_version` 撤销机制，用于用户禁用、租户暂停/删除、角色撤销后的访问收敛。
- token refresh 和会话管理可以分阶段实现，但撤销检查接口必须在 core auth 契约中预留。
