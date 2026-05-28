# Core App Registry

## Progress

- Status: `connected`
- Done: app loader、typed `AppModule` 校验、dependency-first 排序、缺失/循环/重复 label 检查、core version/capability gate 和 CLI/readiness 共用装载诊断已接入。
- Next:
  - [ ] 将 capability 列表与真实外部 provider/部署 profile 的启用状态绑定。

## 职责

App Registry 负责加载、校验和注册所有 app module。它是 core 和业务 app 之间的唯一装配边界。

## 目录建议

```text
src/core/apps/
  module.py
  registry.py
  dependencies.py
  loader.py
```

## AppModule 字段

```text
label
version
min_core_version
dependencies
required_capabilities
provided_capabilities
routers
models
migrations
permissions
event_handlers
task_handlers
schedules
auth_session_store
public_api
admin_models
admin_routes
dashboard_widgets
admin_permissions
```

`AppModule` 是 app 集成的单一事实源。迁移、权限、事件、任务、调度和后台能力不能再各自发明一套注册字段。

字段说明：

- `label`：稳定 app 标识，不能随意重命名。
- `version`：app contract version，用于迁移、兼容和诊断。
- `min_core_version`：可选的 core runtime 最低版本要求；不满足时 AppRegistry 启动前失败。
- `dependencies`：显式声明依赖的 app label。
- `required_capabilities`：app 需要 runtime 提供的可选能力，例如搜索、外部队列或特定 provider。
- `provided_capabilities`：app 对其他 app 或运维诊断暴露的能力标签。
- `routers`：对外 API router。
- `models`：ORM model module 列表。
- `migrations`：迁移路径和依赖声明。
- `permissions`：PermissionSpec 列表。
- `event_handlers`：事件 handler 列表。
- `task_handlers`：任务 handler 列表。
- `schedules`：调度定义列表。
- `auth_session_store`：可选的 `AuthSessionStore` factory 导入路径，用于 server runtime 自动装配请求安全流水线。
- `public_api`：允许其他 app 调用的公开 service/interface。
- `admin_models`：后台模型视图声明。
- `admin_routes`：后台专用路由声明。
- `dashboard_widgets`：后台面板组件声明。
- `admin_permissions`：不绑定具体模型或路由的后台权限声明。

## 加载流程

```text
读取 settings.installed_apps
  -> run app conformance checks
  -> import module path
  -> 获取 module 对象
  -> 校验 label 唯一
  -> import ORM model modules
  -> 收集 routers
  -> 收集 migrations 和依赖
  -> 收集 permissions
  -> 收集 event/task handlers/schedules
  -> 收集 auth session store 声明
  -> 收集 admin metadata
```

`installed_apps` 推荐填写包路径或 `module.py` 路径，例如：

```text
apps.example_domain
apps.example_domain.module
```

如果填写包路径，包的 `__init__.py` 必须转导 `module` 对象。框架只读取 `AppModule` 元数据，不允许 core 直接 import 具体业务实现。

加载完成后，registry 必须使用统一的 app dependency graph 做校验和排序：

- label 全局唯一。
- dependencies 必须能在已安装 app 中解析。
- 依赖环必须启动前失败。
- `registry.modules` 必须按 dependency-first 顺序输出，不依赖 settings 中的人工排列。
- 迁移、权限、事件、任务和调度注册应复用这个顺序，避免各模块重复实现依赖治理。
- AppRegistry 必须在依赖排序后执行 core version/capability gate；不兼容 app 不进入 runtime 装配。
- AppRegistry 必须维护 `diagnostics`，包含 module path、label、version、load_order、runtime capabilities、缺失 capability 和错误列表，供 `list-apps` 与 `/readyz` 复用。
- `create_app()` 必须把 `PermissionRegistry`、`MigrationRegistry`、`EventRegistry`、`TaskRegistry`、`ScheduleRegistry` 和 `AdminRegistry` 从同一个 `AppRegistry` 装配到 `app.state`。
- 同一运行时只能有一个 app 声明 `auth_session_store`；如需替换认证事实来源，使用新的账号 app 或显式传入 `request_security_pipeline`。

## 设计要求

- app 加载错误要明确指向模块路径。
- app label 必须稳定，不能随意改名。
- app 之间不能通过导入顺序隐式依赖。
- 依赖关系必须在 module metadata 中显式声明。
- 业务 app 只能导入已声明 dependency 的其他 app 或平台 app 的 `public_api`，不能导入内部 models/repositories/services。
- registry 必须提供 import lint 或 contract test，检查依赖图、循环依赖和非法跨 app 导入。
- 启动期必须拒绝 conformance 失败的 app；`core check-app` 是人工诊断入口，不是唯一门禁。
