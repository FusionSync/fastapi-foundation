# Core MQ

## Progress

- Status: `connected`
- Done: 通用 MQ publish/consume 数据结构、RabbitMQ async client、`DEPENDENCIES__RABBITMQ_URL` runtime 装配、`/readyz` 探活、startup diagnostics、runtime capability、dependency probe、`core mq check/publish-json/consume-one` CLI、Celery task provider 投递和 outbox 到 RabbitMQ relay 已落地。
- Next: 生产级消费循环和死信队列策略。

## 职责

MQ 模块提供通用消息 broker client 能力。它只负责基础 publish/consume 和连通性，不直接等同于后台任务系统；任务队列仍通过 `core.tasks` 抽象进入，Celery/RabbitMQ provider 必须复用任务系统的 `TaskEnvelope`、幂等和租户生命周期 gate。

## 配置

```env
DEPENDENCIES__RABBITMQ_URL=amqp://user:password@rabbitmq:5672/%2F
```

配置后，app runtime 会创建 RabbitMQ client，挂载到 `app.state.rabbitmq_client`，并在 `/readyz` 中执行 RabbitMQ 连接/通道探活。未配置时不会连接 RabbitMQ，也不会影响 readiness。

## CLI

```bash
core mq check --url amqp://user:password@rabbitmq:5672/%2F --json
core mq publish-json --queue foundation.test --payload-json '{"hello":"world"}' --json
core mq consume-one --queue foundation.test --timeout-seconds 1 --json
```

CLI 输出会脱敏 URL password。`publish-json` 默认使用 RabbitMQ default exchange，`queue` 同时作为声明队列和默认 routing key。

## 边界

- 业务 app 不直接依赖 `aio-pika`，只依赖 `core.mq` 的请求/响应对象。
- RabbitMQ 目前是可选基础能力，不是 private/cloud profile 的强制依赖。
- outbox relay 通过 `RabbitMqOutboxPublisher` 复用 `MqPublishRequest`，不会让 outbox 直接依赖 `aio-pika`。
- Kafka 不在当前 baseline；需要事件流时优先通过 outbox relay 适配。
