# Core Messages

## Progress

- Status: `connected`
- Done: 错误 message catalog、普通 translation catalog、模块标准定义 helper、resolver、exception code registry metadata gate、i18n fallback、message coverage/exclusion 校验、业务 app catalog 注册和 Babel-compatible `.po` 导出已落地。
- Next: _none_

## 职责

Messages 模块负责两类服务端文案：

- API 错误响应 message：以稳定错误码为 key。
- 普通服务端文案 translation：以英文 source string 为 key。

## 与 Exceptions 的关系

```text
Exceptions
  抛出 code、details 和可选 message。

Messages
  根据 code、locale 和部署配置解析最终 message。

Translations
  根据 source string、domain、locale 和 params 解析普通服务端文案。
```

## 目录建议

```text
src/core/messages/
  catalog.py
  resolver.py
  translations.py
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
- app 可以注册自己的 error message catalog，业务模块默认在 `error_messages.py` 使用 `ModuleMessageCatalog` 和 `define_module_message_catalogs()` 定义。
- app 可以注册自己的普通 translation catalog，业务模块按需在可选 `translations.py` 使用 `ModuleTranslationCatalog` 和 `define_module_translation_catalogs()` 定义。
- 未提供 message 时由 core 根据 code 解析。
- message 不允许包含敏感信息。
- code registry 是错误码单一事实源，必须包含默认 HTTP status、details schema、owner module 和废弃状态。
- app 注册 message catalog 时不能创建重复语义 code；CI 必须检查 code 唯一性。
- 每个 locale catalog 必须覆盖本 app 的非废弃错误码；确实不提供该 locale 文案时，必须在 `excluded_codes` 显式排除。

## 当前实现

错误 message 已落地 `ModuleMessageCatalog`、`define_module_message_catalogs()`、`MessageCatalog`、`MessageRegistry` 和 `resolve_message()`：

- 默认 `zh-CN` catalog 由 `core.exceptions` 错误码 registry 自动生成。
- 默认 `en-US` catalog 覆盖核心错误码的英文文案。
- 业务 app 通过 `AppModule.message_catalogs` 声明自己的 `MessageCatalog`，`AppRegistry.load()` 会在注册 `error_codes` 后统一注册这些 catalog。
- `define_module_message_catalogs()` 会校验 message code、`excluded_codes`、owner 和 locale 覆盖；缺少非废弃 code 时必须补 message 或写入 `excluded_codes`。
- `MessageRegistry.register()` 会校验每个 message code 和 excluded code 已进入 exception code registry，且 `owner_module` 与 `ErrorCodeSpec.owner_module` 一致；废弃错误码不能再注册新的 message catalog。
- 未命中精确 locale 时，会先按语言前缀 fallback，例如 `en-GB -> en-US`，再回退到默认 `zh-CN` catalog，最后回退到对应 `ErrorCodeSpec.default_message`。
- 业务 app 错误码通过 `AppModule.error_codes` 注册后，未显式配置 message catalog 时会回退到对应 `ErrorCodeSpec.default_message`。
- `MessageRegistry.register()` 按 `locale + code` 检查重复；相同 message 可幂等重复注册，冲突文案除非显式 `replace=True` 否则拒绝。
- `MessageCatalog` 会拒绝空 locale、空 owner、空 message，以及包含 password、token、secret 等敏感词的文案。
- `check_app()` 会检查 app message catalog 的 owner、code 是否属于本 app 的 `error_codes`，并拒绝为 deprecated code 注册文案、漏写非废弃 code、或同一 code 同时定义和排除。
- `core.serialization.fail()` 在未传入 message 时会自动根据 code 和 locale 解析文案。
- `core.exceptions` handler 在 `AppError` 未显式传 message 时自动使用 resolver；显式业务 message 会被保留。

普通 translation 已落地：

- `TranslationCatalog(locale, domain, owner_module, messages)`。
- `ModuleTranslationCatalog(locale, messages, domain=None)`。
- `define_module_translation_catalogs()` 会把 owner 和默认 domain 固定为 app label。
- `translate()` 和 `gettext()` 根据 locale/domain/source string 返回翻译，未命中时回退 source string。
- `core i18n export-babel` 会从已安装 app 收集 translation catalog 并导出 Babel/gettext 兼容 `.po` 文件。

详细使用方式见 [Error Responses](error-responses.md) 和 [Internationalization](i18n.md)。
