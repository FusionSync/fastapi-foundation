# Authorization & Roles（权限与角色）

这份说明用于新手快速判断：“为什么 403、为什么要写 `PermissionSpec`、角色怎么生效”。

## 1）框架权限层级（先背下来）

`route permission`（路由声明） -> `PermissionSpec`（模块声明） -> `RoleGrant`（主体授权） -> `ProjectedPolicy`（运行时投影）

路由里出现的每个字符串都必须是 `resource:action`，例如：
- `tenant:manage`
- `book:write`
- `role_grant:grant`

## 2）`PermissionSpec` 的真实字段与约束

```python
from core.permissions import PermissionSpec

PermissionSpec(
    resource="book",
    action="write",
    scope="tenant",                # tenant / own / resource / platform
    description="Create/update books",
    risk_level="high",             # low/normal/high/critical
)
```

- `scope` 合法值：`tenant`、`own`、`resource`、`platform`
- `create_router` 的 `permission_scope` 仅支持 `tenant` 或 `platform`
- `create_router` 不允许 `public=True` 同时声明权限

### create_router 与权限的关系（最容易错）

- 有 `permissions` 时，如果不写 `permission_scope`，默认就是 `tenant`
- 非 `public` 且 `permissions` 为空时，不会进行细粒度权限校验
- 公开路由只能是：
  - `create_router(..., public=True)`  
  - 且不能带 `permissions` 或 `permission_scope`

## 3）路由权限怎么对齐（实操）

在路由文件：

```python
from core.base import create_router

book_read_router = create_router(
    "/books",
    tags=["books"],
    permissions=["book:read"],
    permission_scope="tenant",
)
```

在模块文件：

```python
PERMISSIONS = [
    PermissionSpec(resource="book", action="read", scope="tenant"),
    PermissionSpec(resource="book", action="write", scope="tenant", risk_level="high"),
]
```

常见不一致导致 `PERMISSION_DENIED`：
- 路由声明了 `book:write` 但 `PermissionSpec` 没有写入
- `scope` 写成 `platform` 而路由实际是 tenant 数据
- 路由 `permission_scope` 与 `PermissionSpec` 的资源域不匹配

## 4）`AuthorizationDecision` 与 `route_authorization_decision`

任何有写行为的接口建议加入：

```python
from typing import Annotated
from fastapi import Depends
from core.permissions import AuthorizationDecision, route_authorization_decision

decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)]
```

`decision` 里可用于日志/审计输出，确认该次请求实际命中的：
- `tenant_id`
- `user_id`
- `resource`
- `action`
- `policy_version`

## 5）角色配置（RoleTemplate + RoleGrant + 投影）

角色和权限在数据库层由 3 张表承载（实际字段见源码模型）：
- `role_templates`：角色定义（`scope + name + version + permissions`）
- `role_grants`：给某个主体发放的角色
- `projected_policies`：投影结果（运行时生效视图）

### 推荐流程：先发放模板，再触发投影

1. 确认角色模板和权限声明已存在（通常在部署/种子步骤维护）
2. 执行角色指派
3. 执行权限投影同步：  
   `core permissions reconcile --installed-app ... --database-url "$DATABASE__URL" --repair --json`

### 角色最常见路径（无需直接操作 `RoleGrant`）

- `POST /api/v1/platform/tenants/{tenant_id}/invitations` payload 有 `role_template_id`
- 用户完成邀请后系统会在 `TENANT_MEMBER` 激活时尝试自动授予角色

### 直接 SQL 的角色排障（高级场景）

如果你是 DBA/运维，且必须离线修复角色，可以按 DB 结构做最小排查：

```sql
-- 查已有角色模板
SELECT id, scope, name, version FROM role_templates ORDER BY scope, name;

-- 查某租户角色映射
SELECT tenant_id, subject_type, subject_id, role_template_id
FROM role_grants
WHERE tenant_id = 'tenant_demo';
```

### 角色变更后为什么会“看不到生效”

1. 没执行 `core permissions reconcile`
2. reconcile 未加 `--repair`（只读模式不会写入投影）
3. 缓存未清（有缓存层时重跑流程并确认 `policy_version`）

## 6）日常排障

- `PERMISSION_DENIED`：先检查路由字符串、模板、scope、`route_authorization_decision`
- `TENANT_CONTEXT_CONFLICT`：`tenant_id` 与 header 不一致
- 新增/修改角色后必须 run `reconcile`
- 读不到角色权限时，优先看 `PermissionSpec` 是否和模块实际路由一致
