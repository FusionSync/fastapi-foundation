# Core API Conventions

## 职责

API Conventions 定义全项目统一的路由、响应、错误、分页、过滤和版本规范。

## 路由前缀

```text
/api/v1
```

## 响应格式

所有 JSON API 统一使用 HTTP 200 返回。业务成功、业务失败、权限失败、参数失败都通过响应体 `code` 区分。

单对象响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "id": "xxx"
  },
  "list": null,
  "pagination": null,
  "details": null,
  "request_id": "req_xxx"
}
```

列表响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": null,
  "list": [],
  "pagination": {
    "total": 0,
    "page": 1,
    "page_size": 20,
    "has_next": false
  },
  "details": null,
  "request_id": "req_xxx"
}
```

业务失败响应：

```json
{
  "code": "PERMISSION_DENIED",
  "message": "无权限访问该资源",
  "data": null,
  "list": null,
  "pagination": null,
  "details": {
    "resource": "workspace",
    "action": "write"
  },
  "request_id": "req_xxx"
}
```

## HTTP 状态码策略

- 应用可捕获的 JSON API 响应一律返回 HTTP 200。
- 二进制下载成功仍返回 HTTP 200 stream，不使用 JSON envelope。
- 下载失败时返回 JSON envelope，HTTP 状态仍为 200。
- 反向代理、网络、进程崩溃、框架未捕获异常可能产生非 200，这属于应用外或兜底故障。
- 监控必须同时记录 HTTP status 和业务 `code`，不能只看 HTTP status。

## 分页规范

```text
page
page_size
```

默认 `page_size=20`，最大值由配置控制。

## 过滤和排序

简单过滤使用 query params：

```text
?status=active&keyword=demo
```

排序使用：

```text
?sort=-created_at,name
```

## 错误码

错误码按模块命名：

```text
OK
AUTH_INVALID_TOKEN
TENANT_NOT_FOUND
PERMISSION_DENIED
FILE_NOT_FOUND
VALIDATION_ERROR
SYSTEM_ERROR
```

## 设计要求

- router 只做入参、依赖和响应，不放复杂业务逻辑。
- service 抛出领域异常，由 core 异常处理器转换为 API 错误。
- 所有响应都应带 request_id。
- 所有 router 返回值必须通过 core response helpers 包装。
- 禁止业务 router 直接返回裸 dict、裸 list 或未封装 Pydantic schema。
