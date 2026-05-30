# 快速上手文档索引（给第一次用户）

这套文档按“能跑起来 → 能开发 → 能运维 → 能扩展”设计：

1) 先把服务跑起来：`index.md`、`quickstart.md`。  
2) 再开始写第一个业务 APP：`guides/new-developer-workbook.md`、`guides/app-development.md`。  
3) 上线前和日常运维：`guides/cli-guide.md`、`guides/quickstart-checklist.md`、`guides/troubleshooting.md`。  
4) 看懂安全与权限：`guides/authentication.md`、`guides/authorization-and-roles.md`。  
5) 做数据建模：`guides/data-relationships.md`。  
6) 需要改框架本身：`architecture.md`、`guides/advanced-architecture.md`。  

## 推荐阅读路径（不同角色）

- 🧑‍💻 新手开发者：
  - `quickstart.md` → `guides/new-developer-workbook.md` → `guides/app-development.md` → `guides/authorization-and-roles.md`
- 🧰 运维/发布负责人：
  - `guides/cli-guide.md` → `guides/quickstart-checklist.md` → `guides/troubleshooting.md`  
- 🏗 架构师：
  - `architecture.md` → `guides/advanced-architecture.md` → `api/app-registry.md` → `api/tenant-isolation.md`
- 🔌 集成工程师：
  - `guides/data-relationships.md` → `api/api-environment.md` → `api/app-registry.md`

## 文档分层

- `index.md` / `quickstart.md`：新手第一步；目标是“快速可运行”。
- `guides/*`（上层目录）：“做事型”文档；目标是给出参数、示例和成功判定。  
- `api/*`：用于系统对接和团队二次开发的契约性文档。  
- `architecture.md` / `guides/advanced-architecture.md`：高级架构约束与扩展策略。

## 文档复核与更新

为避免文档长期反复编辑而缺少版本管理，新增了 `guides/documentation-todolist.md`：
- 记录每篇文档的完成状态（已完成 / 未完成）。
- 记录每篇文档是否需要复核（待复核 / 已复核）。
- 每次改完文档后在该清单同步状态与日期。
