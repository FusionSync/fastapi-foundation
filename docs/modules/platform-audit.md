# Platform App: Audit

## 职责

Audit 负责记录关键操作、授权失败、文件访问、任务执行和业务资源变更。

## 核心模型

```text
AuditLog
  id
  tenant_id
  actor_id
  action
  resource_type
  resource_id
  request_id
  ip_address
  user_agent
  payload
  created_at
```

## 关键事件

- 登录成功/失败。
- 文件上传/下载/删除。
- 业务资源创建、修改、删除。
- 任务提交、成功、失败。
- 权限拒绝。
- 管理员配置变更。

## 设计要求

- 审计记录写入不能阻塞主流程太久。
- 生产环境审计日志不可随业务删除。
- 敏感字段必须脱敏。
- 私有化部署需要支持导出审计记录。
