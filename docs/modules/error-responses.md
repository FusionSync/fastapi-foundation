# Error Responses

## Progress

- Status: `connected`
- Done: AppError、稳定错误码、模块错误码定义、错误 message catalog、locale 解析入口和统一 response envelope 已接入。
- Next: _none_

## 目标

错误响应是 API 契约，不是普通文本翻译。客户端必须依赖稳定 `code`，不能依赖 `message`。

```json
{
  "code": "ORDERS_NOT_FOUND",
  "message": "订单不存在",
  "data": null,
  "list": null,
  "pagination": null,
  "details": {"order_id": "ord_1"},
  "request_id": "req_x"
}
```

## 定义错误码

业务模块在 `errors.py` 定义错误码：

```python
from core.exceptions import ModuleErrorCode, define_module_error_codes

ORDERS_NOT_FOUND = "ORDERS_NOT_FOUND"

ERROR_CODES = define_module_error_codes(
    "orders",
    ModuleErrorCode(
        ORDERS_NOT_FOUND,
        404,
        "订单不存在",
        details_schema={"order_id": "str"},
    ),
)
```

`define_module_error_codes()` 默认要求 code 以模块 label 的大写前缀开头，例如 `orders -> ORDERS_`。

## 定义错误文案

业务模块在 `error_messages.py` 定义错误码对应的多语言文案：

```python
from core.messages import ModuleMessageCatalog, define_module_message_catalogs
from .errors import ERROR_CODES, ORDERS_NOT_FOUND

MESSAGE_CATALOGS = define_module_message_catalogs(
    "orders",
    error_codes=ERROR_CODES,
    catalogs=[
        ModuleMessageCatalog(
            locale="zh-CN",
            messages={ORDERS_NOT_FOUND: "订单不存在"},
        ),
        ModuleMessageCatalog(
            locale="en-US",
            messages={ORDERS_NOT_FOUND: "Order not found"},
        ),
    ],
)
```

每个 locale 必须覆盖本模块所有非废弃错误码。确实不提供某个 locale 文案时，必须显式写入 `excluded_codes`：

```python
ModuleMessageCatalog(
    locale="en-US",
    messages={ORDERS_NOT_FOUND: "Order not found"},
    excluded_codes=["ORDERS_LEGACY_ERROR"],
)
```

## 注册到模块

```python
from .errors import ERROR_CODES
from .error_messages import MESSAGE_CATALOGS
from core.apps import AppModule

module = AppModule(
    label="orders",
    version="0.1.0",
    error_codes=ERROR_CODES,
    message_catalogs=MESSAGE_CATALOGS,
)
```

`check_app()` 和 `AppRegistry.load()` 会拒绝：

- 未声明的错误码。
- owner 与 app label 不一致。
- deprecated code 新增文案。
- locale 漏写非废弃 code 且未写 `excluded_codes`。
- 同一个 code 同时出现在 `messages` 和 `excluded_codes`。

## 抛出错误

业务代码只抛稳定 code：

```python
from core.exceptions import AppError
from .errors import ORDERS_NOT_FOUND

raise AppError(
    ORDERS_NOT_FOUND,
    details={"order_id": order_id},
)
```

不要在 service 中拼接多语言文本。显式传入 `message` 会绕过 catalog，只用于极少数动态系统错误。

## 运行时解析

异常 handler 会把 `AppError.code` 解析为当前 locale 的 message。未命中时 fallback 顺序是：

```text
精确 locale -> 同语言 catalog -> 默认 zh-CN catalog -> ErrorCodeSpec.default_message
```

客户端逻辑必须判断 `code`，展示层可以使用 `message`。
