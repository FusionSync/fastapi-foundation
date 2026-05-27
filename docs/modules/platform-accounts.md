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

UserCredential
  user_id
  password_hash
  password_updated_at

ExternalIdentity
  user_id
  provider
  subject
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
