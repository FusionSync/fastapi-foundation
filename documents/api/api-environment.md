# API Envelope & Error Contract

所有 JSON 响应采用统一 Envelope（`code/message/data/list/pagination/details/request_id`）。

- 200/4xx/5xx 错误都以稳定 `code` 形式返回。
- 建议直接复用 `core.serialization.ok` / `fail`。
