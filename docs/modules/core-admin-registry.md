# Core Admin Registry

## Progress

- Status: `partial`
- Done: `AdminModelSpec` 和 `AdminRegistry.from_app_registry()` 已能从 app metadata 汇总后台注册信息。
- Next:
  - [ ] 接入真实 admin UI/route protection。
  - [ ] 为 app 声明的 admin 权限补 contract tests。

## 职责

Admin Registry 负责管理后台能力的注册，让 platform app 和 business app 可以各自声明自己的 admin view。

## 目录建议

```text
src/core/admin/
  specs.py
  registry.py
  __init__.py
```

## 目标

- 不把所有 SQLAdmin view 堆在一个文件。
- app 自己拥有自己的 admin 配置。
- core 统一挂载 admin 后台。
- admin 权限和普通 API 权限分离。

## 注册内容

```text
model_admins
dashboard_widgets
admin_routes
admin_permissions
```

这些注册项由各 app 的 `AppModule` 声明，`AdminRegistry.from_app_registry()` 统一汇聚。

## Spec 契约

- `AdminModelSpec`：声明一个后台模型视图，包含 `admin_id`、`model_path`、`label`、`permissions`、`tenant_scoped`、`read_only`。
- `AdminRouteSpec`：声明一个后台专用路由，`path` 必须以 `/admin` 开头，HTTP method 只允许 `GET`、`POST`、`PUT`、`PATCH`、`DELETE`。
- `AdminDashboardWidgetSpec`：声明后台首页或运维面板组件，包含 `widget_id`、`title`、`provider_path`、`permissions`。
- `AdminPermissionSpec`：声明后台权限点，`resource` 不允许手写 `admin:` 前缀。

`AdminPermissionSpec.to_permission_spec()` 会转换为普通权限目录中的 `PermissionSpec`：

```text
resource = admin:<resource>
scope = platform
risk_level defaults to high unless overridden
```

这样后台权限天然与业务 API 权限分离，并统一进入权限目录，为后续权限投影、审计和策略治理提供同一份 metadata。

## Registry 行为

- `AdminRegistry.register()` 拒绝重复的 `admin_id`、`route_id`、`widget_id`。
- `AdminRegistry.register()` 对相同 `(resource, action)` 的后台权限去重。
- `AdminRegistry.register()` 拒绝相同 `(resource, action)` 但 description 或 risk_level 不一致的后台权限。
- `AdminRegistry.permission_specs()` 输出平台级 `PermissionSpec`，供权限目录收集。
- `AdminRegistry.to_dict()` 输出可序列化元数据，包含后台 surface 和完整后台权限目录，供未来 admin UI、CLI 或诊断接口使用。

后台权限 metadata 冲突属于启动期契约错误。权限目录可以报告该错误，但业务应在部署前通过 contract test 或 CLI 检查修复。
app conformance 会在启动前导入 `AdminModelSpec.model_path`、`AdminRouteSpec.handler_path` 和 `AdminDashboardWidgetSpec.provider_path`，并把不可导入或不可调用的错误定位到具体 admin id 和 dotted path。

## 设计要求

- 后台只面向内部管理和运维，不作为业务主前端。
- admin 操作必须写审计日志。
- 跨租户管理能力必须要求 platform admin 权限。
- 私有化部署可按配置关闭 admin。
- core 只负责注册和契约，不绑定具体 admin UI 框架；SQLAdmin、FastAPI route 或自研前端都必须通过同一套 spec 接入。
