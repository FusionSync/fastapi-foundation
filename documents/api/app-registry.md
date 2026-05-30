# App Registry (对外)

核心框架通过 `AppModule` 和 `AppRegistry` 统一装配所有应用。

- 只读 `module.py` 中的 `module` 元数据。
- 装载顺序按依赖图计算。
- 启动期会阻断不合规模块，避免运行时“后验”错误。
