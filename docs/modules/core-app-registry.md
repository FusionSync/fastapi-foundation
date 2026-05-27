# Core App Registry

## 职责

App Registry 负责加载、校验和注册所有 app module。它是 core 和业务 app 之间的唯一装配边界。

## 目录建议

```text
src/core/apps/
  module.py
  registry.py
  loader.py
```

## AppModule 字段

```text
label
routers
orm_apps
permissions
event_handlers
task_handlers
startup_hooks
shutdown_hooks
```

## 加载流程

```text
读取 settings.installed_apps
  -> import module path
  -> 获取 module 对象
  -> 校验 label 唯一
  -> 收集 routers
  -> 收集 ORM models
  -> 收集 permissions
  -> 收集 event/task handlers
```

## 设计要求

- app 加载错误要明确指向模块路径。
- app label 必须稳定，不能随意改名。
- app 之间不能通过导入顺序隐式依赖。
- 依赖关系必须在 module metadata 中显式声明。
