# Platform App: Audit

## 职责

Audit 负责记录关键操作、授权失败、文件访问、任务执行和业务资源变更。

## 核心模型

```text
AuditLog
  id
  tenant_id
  actor_id
  actor_type
  auth_provider
  session_id
  action
  resource_type
  resource_id
  result
  reason
  policy_version
  request_id
  ip_address
  user_agent
  payload
  hash_prev
  hash
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
- 安全关键审计必须与业务或权限变更强一致写入，不能仅依赖 best-effort 异步事件。
- 生产 profile 应支持 hash chain 或外部 WORM/SIEM 适配，保证审计记录可追溯篡改。
- 审计保留、导出和删除策略必须按部署 profile 配置。
