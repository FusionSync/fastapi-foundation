# FastAPI Foundation

这是一个可扩展 FastAPI 后台项目框架的设计仓库。仓库根目录就是后端项目，不再额外创建 `backend/` 目录。

当前阶段先沉淀框架设计文档，目标是把可长期维护的核心框架边界定清楚，再逐步落地代码和业务 app。

## 设计目标

- `core` 只承载通用框架能力，不放具体业务。
- 平台能力和业务能力都通过 app 模块注册，框架启动逻辑不依赖具体业务模块。
- 配置、数据库、认证、权限、租户、存储、任务队列、审计、事件等基础设施集中维护。
- 支持本地开发、私有化部署和公网 SaaS 三种运行形态。
- ORM 采用 SQLAlchemy 2.x async，迁移采用 Alembic。

## 文档入口

- [总体架构设计](docs/architecture/00-overall-design.md)
- [基础框架演进路线图](docs/architecture/01-foundation-roadmap.md)
- [模块文档索引](docs/modules/00-index.md)
- [运维文档索引](docs/operations/00-index.md)
- [核心与应用边界决策](docs/decisions/0001-core-and-app-boundary.md)

## 推荐技术基线

- FastAPI
- SQLAlchemy 2.x async
- Alembic
- PostgreSQL，单机版可切 SQLite
- Redis
- Casbin
- Local JWT / Logto / Keycloak 可插拔认证
- 本地文件系统 / MinIO / S3 可插拔存储
- RQ 或 Celery 任务队列
- SQLAdmin 内部管理后台

## 目录约定

未来代码目录建议如下：

```text
src/
  core/
  platform_apps/
  apps/
server/
  main.py
docs/
  architecture/
  modules/
  operations/
  decisions/
```

`core` 不导入具体业务 app；业务 app 只通过 `module.py` 注册路由、模型、权限点、事件处理器和任务处理器。
