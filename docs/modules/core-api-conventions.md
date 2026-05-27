# Core API Conventions

## 职责

API Conventions 定义全项目统一的路由、响应、错误、分页、过滤和版本规范。

## 路由前缀

```text
/api/v1
```

## 响应格式

成功响应可以直接返回业务 schema；列表接口统一返回分页对象：

```text
items
total
page
page_size
```

错误响应统一包含：

```text
code
message
details
request_id
```

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
AUTH_INVALID_TOKEN
TENANT_NOT_FOUND
PERMISSION_DENIED
FILE_NOT_FOUND
VALIDATION_ERROR
```

## 设计要求

- router 只做入参、依赖和响应，不放复杂业务逻辑。
- service 抛出领域异常，由 core 异常处理器转换为 API 错误。
- 所有响应都应带 request_id。
