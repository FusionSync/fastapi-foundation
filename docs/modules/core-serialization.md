# Core Serialization

## Progress

- Status: `connected`
- Done: `to_jsonable()`、`ok()`、`ok_list()`、`fail()`、Envelope/ListEnvelope 和复杂类型序列化规则已落地。
- Next:
  - [ ] 补复杂类型 golden examples 和 OpenAPI schema regression。
  - [ ] 明确 streaming/binary response 与 envelope 的例外边界。

## 职责

Serialization 模块负责统一 JSON 编码、模型导出、响应封装和特殊类型序列化。

## 与 Schema 的关系

Schema 是 API 契约，负责输入输出字段、校验和文档。

Serialization 是编码策略，负责把 Python 对象稳定地转成 JSON 可传输结构。

```text
schemas.py
  定义这个接口有哪些字段。

core.serialization
  定义 datetime、Decimal、UUID、Enum、ORM model 如何输出。
```

两者关系是：schema 使用 serialization 策略，但 serialization 不替代 schema。

## 目录建议

```text
src/core/serialization/
  encoders.py
  responses.py
  json.py
```

## 统一策略

- `datetime` 使用 ISO 8601，必须带时区；naive datetime 在 API 输出前视为错误。
- 默认存储和输出使用 UTC；如保留 offset，必须在 schema 中显式说明。
- `date` 使用 `YYYY-MM-DD`。
- `Decimal` 默认转字符串，避免精度丢失。
- `UUID` 转字符串。
- `Enum` 输出 value。
- ORM model 不直接裸返回，必须经过 schema 或 serializer。
- Pydantic 输出统一使用 `model_dump(mode="json", by_alias=True)`。
- 默认不使用 `exclude_none=True` 影响 envelope 字段；envelope 未使用字段显式为 `null`。

当前实现提供 `to_jsonable()`，并已接入 `ok()`、`ok_list()` 和 `fail()`：

- aware `datetime` 输出 `isoformat()`，naive `datetime` 抛 `SYSTEM_ERROR`。
- `date`、`Decimal`、`UUID`、`Enum` 和嵌套 list/dict 会递归编码。
- Pydantic model 先 `model_dump(mode="python", by_alias=True)`，再经过统一编码。
- OpenAPI 响应模型使用 `Envelope[T]` 和 `ListEnvelope[T]` 泛型绑定具体 payload schema；运行时仍通过 `ok()` / `ok_list()` 输出同一 envelope 字段。

## BaseSchema 关系

`core.base.schemas.BaseSchema` 应复用 serialization 配置：

```text
from_attributes = True
populate_by_name = True
json_encoders = core serialization encoders
```

## 响应封装

统一响应 helpers 放在 serialization 或 response 模块中：

```text
ok(data)
ok_list(items, pagination)
fail(code, message=None, details=None, status_code=None, headers=None)
```

## 设计要求

- 业务 router 禁止直接返回裸 ORM model。
- 业务 router 禁止直接返回裸 dict/list。
- 所有 JSON 响应必须经过 response helper。
- router 应声明 `response_model=Envelope[ReadSchema]` 或 `response_model=ListEnvelope[ReadSchema]`，避免 OpenAPI 退化为裸 object。
- 文件下载、流式响应不走 JSON envelope，但失败时仍走 JSON envelope。
