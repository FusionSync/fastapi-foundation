# Documentation Checklist（文档进度清单）

用于记录文档可交付状态，避免重复误改。每次改完文档后同步更新状态。

| 文档 | 状态 | 复核状态 | 最后更新 | 备注 |
|---|---|---|---|---|
| [documents/README.md](../README.md) | ✅ 完成 | ✅ 已复核 | 2026-05-30 | 入口与阅读策略 |
| [documents/index.md](../index.md) | ✅ 完成 | ✅ 已复核 | 2026-05-30 | 新手路径与入口入口 |
| [documents/quickstart.md](../quickstart.md) | ✅ 完成 | ✅ 已复核 | 2026-05-30 | 上手 + 配置 + 登录验证 |
| [guides/authentication.md](../guides/authentication.md) | ✅ 完成 | ✅ 已复核 | 2026-05-30 | 认证与 tenant 解析 |
| [guides/authorization-and-roles.md](../guides/authorization-and-roles.md) | ✅ 完成 | ✅ 已复核 | 2026-05-30 | 权限/角色映射 |
| [guides/data-relationships.md](../guides/data-relationships.md) | ✅ 完成 | ✅ 已复核 | 2026-05-30 | 模型关系与查询边界 |
| [guides/app-development.md](../guides/app-development.md) | ✅ 完成 | ✅ 已复核 | 2026-05-30 | 业务模块开发模板 |
| [guides/cli-guide.md](../guides/cli-guide.md) | ✅ 完成 | ✅ 已复核 | 2026-05-30 | CLI 参数与示例 |
| [guides/new-developer-workbook.md](../guides/new-developer-workbook.md) | ⏳ 待复核 | ⏳ 待复核 | 2026-05-30 | 已有内容较完整，建议同步参数说明 |
| [guides/quickstart-checklist.md](../guides/quickstart-checklist.md) | ⏳ 待复核 | ⏳ 待复核 | 2026-05-30 | 可增加“文档自检 + 运维自检”双清单 |
| [guides/troubleshooting.md](../guides/troubleshooting.md) | ✅ 完成 | ☐ 待复核 | 2026-05-30 | 常见报错与处理 |

## 维护规则

- 每次发布前：至少把 **待复核** 项变为**已复核**。
- 若新增文档，先写入此表再进入文档正文审阅。
- 若改动一个接口或参数定义，必须同时更新：
  - 对应模块指南
  - 相关 API/CLI 说明
  - 本清单中的状态
