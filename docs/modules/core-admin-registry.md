# Core Admin Registry

## 职责

Admin Registry 负责管理后台能力的注册，让 platform app 和 business app 可以各自声明自己的 admin view。

## 目录建议

```text
src/core/admin/
  registry.py
  views.py
  permissions.py
```

## 目标

- 不把所有 SQLAdmin view 堆在一个文件。
- app 自己拥有自己的 admin 配置。
- core 统一挂载 admin 后台。
- admin 权限和普通 API 权限分离。

## 注册内容

```text
model_admins
dashboard_widgets
admin_routes
admin_permissions
```

## 设计要求

- 后台只面向内部管理和运维，不作为业务主前端。
- admin 操作必须写审计日志。
- 跨租户管理能力必须要求 platform admin 权限。
- 私有化部署可按配置关闭 admin。
