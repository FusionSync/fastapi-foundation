# Core Permissions

## 职责

Permissions 模块负责统一授权接口，第一版底层使用 Casbin。

## 权限模型

使用 RBAC with Domains：

```text
subject = user_id
domain = tenant_id
object = resource
action = action
```

示例：

```text
alice, tenant_a, workspace, write -> allow
alice, tenant_b, workspace, write -> deny
```

## 目录建议

```text
src/core/permissions/
  enforcer.py
  deps.py
  model.conf
  registry.py
```

## 业务调用方式

业务 app 只调用 core 抽象：

```python
await authorize(
    user_id=current_user.id,
    tenant_id=current_tenant.id,
    resource="workspace",
    action="write",
)
```

禁止业务 app 直接操作 Casbin enforcer。
平台级权限使用 `scope=platform` 的 RoleGrant，不允许通过 `CurrentUser.is_platform_admin` 绕过授权接口。

## 权限点注册

每个 app 在 `module.py` 中声明权限点：

```python
permissions=[
    PermissionSpec(resource="workspace", action="read", scope="tenant"),
    PermissionSpec(resource="workspace", action="write", scope="tenant"),
    PermissionSpec(resource="file", action="upload", scope="tenant"),
]
```

core 启动时可收集权限点用于初始化、校验和后台展示。

CLI 可查看权限目录：

```bash
core permissions catalog --installed-app apps.example_domain.module --json
core permissions reconcile --installed-app apps.example_domain.module --json
```

`catalog` 来自 app module 的 `PermissionSpec`。`reconcile` 的数据库修复能力由 `PolicyProjector.reconcile(repair=True)` 提供，CLI 当前先提供 metadata contract，用于部署前检查权限目录是否可收集。

## 角色建议

第一版内置：

```text
owner
admin
editor
viewer
```

业务 app 可以额外声明角色模板，但最终都应落到权限点。

## 审计要求

所有授权失败必须记录：

- user_id
- tenant_id
- resource
- action
- request_id
- route
