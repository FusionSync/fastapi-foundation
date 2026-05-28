# 总体架构设计

## 1. 背景

系统目标是构建一个可长期维护、可扩展、可私有化部署的 FastAPI 后台项目框架。框架本身只解决通用后台能力：启动装配、配置、数据库、认证、权限、多租户、文件、任务、事件、审计、日志和 API 约定。

系统需要同时支持三种运行形态：

- 本地开发/单机版：本地 API 服务、SQLite 或本地 PostgreSQL、本地文件存储。
- 私有化部署：部署在客户内网，支持企业认证、内网存储和内网依赖服务。
- 公网 SaaS：统一云端服务，多租户隔离，支持横向扩展。

## 2. 核心原则

- 根目录即后端项目，不使用额外 `backend/` 包装目录。
- `core` 只提供通用框架能力，不包含具体业务。
- 平台能力和业务能力都作为 app 注册，不直接耦合框架启动逻辑。
- 所有配置集中在 `core.config`，业务 app 不直接读取环境变量。
- 所有租户数据必须带 `tenant_id`，所有业务查询默认在租户上下文中执行。
- 请求进入 route 时必须初始化 ContextVar 请求上下文，后续 service、审计、日志、权限从上下文读取 request/user/tenant 信息。
- 所有业务 app 必须遵守标准目录结构：`schemas.py`、`models.py`、`router.py`、`services.py`。
- 业务 app 通过公开 service、事件或接口协作，不直接访问对方内部实现。
- 框架能力必须可替换：认证 provider、存储 provider、任务 provider、权限 provider 都不能写死。
- JSON API 统一响应 envelope，HTTP status 表达协议/权限/系统语义，响应体 `code` 表达稳定业务语义。

## 3. 分层架构

```text
统一 API 层
  FastAPI routers、认证依赖、租户依赖、权限依赖、响应封装

平台 app 层
  账号、租户、文件、审计、系统配置、管理后台

业务 app 层
  由具体项目按 app contract 扩展，例如 CRM、合同、知识库、订单、审批等

核心框架层
  配置、App Factory、Context、Base Classes、ORM、租户隔离、迁移治理、认证抽象、权限抽象、缓存、锁、幂等、限流、配额、存储抽象、HTTP 客户端、任务抽象、调度抽象、事务性 Outbox、序列化、消息、日志、异常

基础设施层
  PostgreSQL/SQLite、Redis、MinIO/S3、本地文件系统、外部认证服务、消息队列
```

## 4. 未来代码结构

```text
src/
  core/
    app/
    base/
    config/
    context/
    db/
    migrations/
    auth/
    security/
    tenancy/
    permissions/
    cache/
    locks/
    idempotency/
    rate_limit/
    quotas/
    storage/
    http_clients/
    tasks/
    scheduler/
    events/
    outbox/
    exceptions/
    serialization/
    messages/
    admin/
    cli/
    testing/
    apps/
    logging/
    observability/

  platform_apps/
    accounts/
    tenants/
    files/
    audit/
    admin/

  apps/
    example_domain/
      schemas.py
      models.py
      router.py
      services.py
      module.py

server/
  main.py
```

## 5. App 注册模型

每个 app 通过 `module.py` 暴露元信息：

```python
module = AppModule(
    label="example_domain",
    version="0.1.0",
    dependencies=[],
    routers=[router],
    models=["apps.example_domain.models"],
    migrations=MigrationSpec(
        path="apps.example_domain.migrations",
        depends_on=[],
    ),
    permissions=[
        PermissionSpec(resource="example", action="read", scope="tenant"),
        PermissionSpec(resource="example", action="write", scope="tenant"),
        PermissionSpec(resource="example", action="delete", scope="tenant"),
    ],
    event_handlers=[],
    task_handlers=[],
    schedules=[],
    public_api=[],
)
```

框架启动时只读取 `settings.installed_apps`，动态加载 app 模块，然后注册路由、ORM models、迁移、权限点、事件处理器、任务处理器和调度定义。

`AppModule` 是唯一注册事实源；迁移、权限、事件、任务和调度不能绕过它单独注册。

## 6. 配置模型

配置全部由 `core.config.settings` 管理，使用 `pydantic-settings` 和嵌套环境变量：

```env
ENV=private
DATABASE__URL=postgres://user:pass@postgres:5432/service_core
AUTH__PROVIDER=logto
AUTH__OIDC_ISSUER=https://auth.example.com/oidc
STORAGE__PROVIDER=s3
STORAGE__S3_BUCKET=service-files
```

业务 app 获取配置必须通过依赖注入或 core 暴露的 settings 对象，不直接解析环境变量。

## 7. 数据模型主线

```text
Tenant
  -> User / TenantMember
  -> FileObject
  -> AuditLog
  -> BusinessResource
```

业务资源必须继承租户边界，并根据需要挂接 `created_by`、`updated_by`、软删除和审计事件。

## 8. 认证与权限

认证 provider 可插拔：

- `local_jwt`：MVP、本地单机版和演示环境。
- `logto`：SaaS/B2B 多租户认证。
- `keycloak`：私有化、政企、LDAP/AD/SSO 场景。

权限通过统一授权接口执行：

```python
await authorize(
    user_id=current_user.id,
    tenant_id=current_tenant.id,
    resource="workspace",
    action="write",
)
```

底层第一版建议用 Casbin RBAC with Domains，其中 domain 对应 `tenant_id`。

## 9. ORM 选择

当前设计选择 SQLAlchemy 2.x async + Alembic：

- SQLAlchemy 生态成熟，事务、连接、Unit of Work 和复杂查询能力更适合长期演进。
- Alembic 迁移能力成熟，更容易做 migration manifest、preflight、dry-run 和 drift check。
- async engine/session 适配 FastAPI，同时保留 Core SQL 能力处理复杂查询。
- ORM 只在 core base/repository 层暴露，业务 app 不直接绑定底层 ORM API。

约束：

- 不在业务层手写跨租户查询。
- 基础模型提供 `tenant_id`、审计字段和软删除策略。
- 复杂事务必须封装在 service 层。
- 业务代码通过 repository/unit-of-work 访问数据库，不直接管理 session。

## 10. API 命名约定

统一前缀：

```text
/api/v1
```

平台接口：

```text
POST /api/v1/auth/login
GET  /api/v1/me
GET  /api/v1/tenants
POST /api/v1/files/upload
GET  /api/v1/files/{id}/download
GET  /api/v1/audit-logs
```

业务 app 自己声明资源路径，但必须遵守统一响应、错误码、分页和权限依赖规范。

JSON API 响应统一封装。单对象响应使用 `data`，列表响应使用 `list` 和 `pagination`：

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "list": null,
  "pagination": null,
  "request_id": "req_xxx"
}
```

业务失败仍使用相同 envelope，但 HTTP status 保留标准语义：

```json
{
  "code": "PERMISSION_DENIED",
  "message": "无权限访问该资源",
  "data": null,
  "list": null,
  "pagination": null,
  "details": {},
  "request_id": "req_xxx"
}
```

## 11. 迭代路线

第一阶段：

- 搭建 core 框架、配置、App 注册、Context、统一响应、ORM、租户隔离基类、基础认证。
- 建立租户、用户、成员、文件、审计基础模型。
- 提供模块开发规范和一个 example app。

第二阶段：

- 接入权限模型升级、租户生命周期、安全模块、缓存、锁、限流。
- 实现 CLI、测试基座、幂等、配额、HTTP 客户端、事务性 Outbox、迁移治理、任务队列、调度器、事件总线、存储 provider。
- 加入 SQLAdmin 或自研内部管理后台。

第三阶段：

- 接入 Logto/Keycloak。
- 接入 MinIO/S3。
- 完善 observability、审计、部署 profile。
- 支持 SaaS、私有化、本地单机三种配置 profile。
