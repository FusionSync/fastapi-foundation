# Core Context

## Progress

- Status: `connected`
- Done: 冻结 `RequestContext`、ContextVar 注入、request id/trace id 传播和 try/finally reset 已落地。
- Next:
  - [ ] 定义 background task、outbox handler 和 scheduler job 的 context handoff。
  - [ ] 将 context 字段接入结构化日志和审计默认字段。

## 职责

Context 模块基于 `contextvars` 维护请求上下文，让 router、service、审计、日志、权限和事件处理能够读取同一份当前请求信息。

## 目录建议

```text
src/core/context/
  vars.py
  context.py
  middleware.py
  deps.py
```

## RequestContext 字段

```text
request_id
trace_id
user_id
tenant_id
locale
ip_address
user_agent
route
method
started_at
```

## 注入流程

```text
请求进入
  -> RequestContextMiddleware 创建 request_id 和基础上下文
  -> Auth dependency 解析 current_user 并写入 context
  -> Tenancy dependency 解析 current_tenant 并写入 context
  -> Permission dependency 使用 context 授权
  -> Service 从 context 读取 user/tenant/request_id
  -> Response helper 带回 request_id
```

## 使用约束

- service 可以读取 context，但不应该修改 context。
- auth 和 tenancy 是允许写 context 的依赖。
- 后台任务没有 HTTP request，必须显式构造 TaskContext。
- 测试中必须提供 context fixture。
- RequestContext 在认证、租户解析和授权完成后必须冻结，业务 service 不允许修改 `user_id`、`tenant_id`、`request_id`。
- middleware 必须使用 `try/finally` reset ContextVar token，避免请求间上下文污染。
- 后台任务必须显式接收 `TaskContext`，不能隐式继承上一个 HTTP 请求上下文。

## 风险

ContextVar 能减少参数传递，但不能成为隐藏全局状态。需要遵守：

- 不在 context 中放大型对象。
- 不在 context 中放数据库连接。
- 请求结束后必须 reset token。
- service 仍应保持核心业务逻辑可测试。
- contract test 必须覆盖 context reset、授权后 tenant_id 不可变和后台任务显式 context。
