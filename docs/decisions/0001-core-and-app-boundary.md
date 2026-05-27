# ADR 0001: Core、Platform Apps 与 Business Apps 边界

## 状态

Accepted

## 背景

系统需要长期支持多个业务方向、公网 SaaS、私有化部署和本地单机版。如果将配置、认证、权限、业务模型和具体业务接口混在同一层，后续会难以维护、难以测试，也难以支持不同部署形态。

## 决策

采用 `core + platform_apps + apps` 三层模块结构：

```text
core
  通用框架能力

platform_apps
  账号、租户、文件、审计等平台能力

apps
  具体业务能力
```

`core` 不直接导入任何业务 app。所有 app 通过 `module.py` 注册路由、ORM models、权限点、事件处理器和任务处理器。

## 结果

收益：

- core 可独立演进，业务 app 可插拔。
- 配置和基础设施集中维护。
- SaaS、私有化、本地单机版共享一套框架。
- 后续接入任何业务时，不需要改动框架启动逻辑。

代价：

- 初期需要多写 module 注册和配置装配代码。
- 业务开发必须遵守边界，不能为了方便直接跨层调用。
- 权限、租户、存储等基础能力需要先定义抽象。

## 约束

- app 可以依赖 core。
- core 不能依赖 app。
- platform app 不能依赖 business app。
- business app 可以依赖 platform app 的公开 service 或接口。
- 所有跨 app 调用优先通过 service 或事件，不直接访问对方内部实现。
