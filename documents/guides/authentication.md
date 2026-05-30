# Authentication（认证与租户上下文）

本页回答四件事：`我怎么登录`、`租户信息怎么拿`、`token 过期怎么办`、`错误码怎么读`。

## 1）认证端点一览（带参数）

| 方法 | 路径 | 鉴权方式 | 请求参数 | 说明 |
|---|---|---|---|---|
| `POST` | `/api/v1/auth/login` | public | `email`, `password`, `tenant_id?` | 登录本地账号 |
| `POST` | `/api/v1/auth/refresh` | Bearer | 无 | 根据当前会话刷新 access token |
| `POST` | `/api/v1/auth/logout` | Bearer | 无 | 注销并吊销会话 |
| `GET` | `/api/v1/auth/external/{provider}/authorize` | public | `provider`, `tenant_id?`, `redirect_after?` | 生成外部登录授权 URL |
| `POST` | `/api/v1/auth/external/{provider}/callback` | public | `code`, `state` | 用授权码换会话 token |
| `GET` | `/api/v1/me` | Bearer | 无 | 当前用户信息 |
| `PATCH` | `/api/v1/me` | Bearer | `display_name` | 更新 display name |
| `PATCH` | `/api/v1/me/password` | Bearer | `current_password`, `new_password` | 修改密码 |
| `GET` | `/api/v1/me/sessions` | Bearer | 无 | 查询当前用户的会话列表 |
| `DELETE` | `/api/v1/me/sessions/{session_id}` | Bearer | `session_id` | 注销某个会话 |

## 2）登录响应结构

`POST /api/v1/auth/login`

请求体：

```json
{
  "email": "admin@example.com",
  "password": "your-password",
  "tenant_id": "tenant_demo"
}
```

响应（`data`）示例：

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 3600,
  "session": {
    "id": "session-id",
    "user_id": "user-id",
    "tenant_id": "tenant_demo",
    "status": "active"
  }
}
```

JWT 的关键 claim：
- `sub`：用户 ID
- `sid`：会话 ID
- `tid`：tenant id（可能为空）
- `provider`：登录方式（如 `local`）
- `exp`：过期时间戳

## 3）tenant 解析链（从请求到执行）

解析顺序如下：

1. token 中的 `tid`
2. 请求头 `X-Tenant-ID`
3. 当前用户默认 tenant（在有上下文时）

当 token 与 header 同时存在但不一致时，返回 `TENANT_CONTEXT_CONFLICT`。

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: tenant_demo" \
  http://127.0.0.1:8000/api/v1/me
```

## 4）常用调用（可直接拷贝）

```bash
# 登录
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"change-me","tenant_id":"tenant_demo"}' \
  | jq -r '.data.access_token')

# 获取当前用户
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/me

# 刷新与登出
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/refresh \
  -H "Authorization: Bearer $TOKEN"
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/logout \
  -H "Authorization: Bearer $TOKEN"

# 外部授权示例（授权页）
curl -s "http://127.0.0.1:8000/api/v1/auth/external/oidc/authorize?tenant_id=tenant_demo&redirect_after=http://localhost:3000/callback"

# 外部回调换 token
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/external/oidc/callback \
  -H "Content-Type: application/json" \
  -d '{"code":"<code>","state":"<state>"}'
```

## 5）登录链路常见错误

- `AUTH_INVALID_TOKEN`
  - 常见于 token 缺失、过期或签名不合法
  - 处理：重新登录或 refresh
- `TENANT_ACCESS_DENIED`
  - token 或 header 都未提供 tenant，且请求需要租户上下文
  - 处理：先补 `tenant_id` / `X-Tenant-ID`
- `TENANT_CONTEXT_CONFLICT`
  - token tenant 与 header tenant 冲突
  - 处理：统一 token 与 header 的租户来源
- `PERMISSION_DENIED`
  - 权限不足或 `PermissionSpec` 与路由不匹配
  - 处理：先走授权章节中的对齐检查

## 6）给新手的安全提示

- 开发环境可临时使用 `change-me-only-local`，生产必须走 secret 管理（如云端密钥服务）
- 不要在脚本中直接硬编码用户账号密码，优先读取 CI secrets
- 所有 auth 场景至少做三类验收：无 token / 错 tenant / 无权限
