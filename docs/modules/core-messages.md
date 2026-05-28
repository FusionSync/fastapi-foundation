# Core Messages

## 职责

Messages 模块负责业务 code 对应的默认 message 管理，支持集中维护、多语言和私有化定制。

## 与 Exceptions 的关系

```text
Exceptions
  抛出 code、details 和可选 message。

Messages
  根据 code、locale 和部署配置解析最终 message。
```

## 目录建议

```text
src/core/messages/
  catalog.py
  resolver.py
  locales/
    zh-CN.yaml
    en-US.yaml
```

## 使用示例

```text
PERMISSION_DENIED -> 无权限访问该资源
VALIDATION_ERROR -> 参数校验失败
RATE_LIMITED -> 请求过于频繁
QUOTA_EXCEEDED -> 已超出配额限制
```

## 设计要求

- code 是稳定接口契约，message 不是。
- app 可以注册自己的 message catalog。
- 未提供 message 时由 core 根据 code 解析。
- message 不允许包含敏感信息。
- code registry 是错误码单一事实源，必须包含默认 HTTP status、details schema、owner module 和废弃状态。
- app 注册 message catalog 时不能创建重复语义 code；CI 必须检查 code 唯一性。
