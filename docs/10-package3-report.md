# Orchestra 包 3 实施报告：Redis Streams + 自研状态机

> 日期：2026-08-20
> 状态：已实现；全量测试 62 项通过（含包 3 新增 7 项）

## 1. 交付内容

包 3 把任务调度从“SQLite + 进程内 asyncio 后台任务”升级为可观测、可重试、可恢复的工作流引擎：

- `WorkflowDriver` 统一接口：`submit / execute / retry / recover / finish / cancel`。
- `SqliteWorkflowDriver`：本地开发/测试默认实现，内置延迟重试调度与启动恢复。
- `RedisStreamWorkflowDriver`：命令流 `orchestra:task:commands`、事件流 `orchestra:task:events`、消费组 `orchestra-workers`。
- `RedisWorkflowWorker`：`XREADGROUP` 并发消费、`XACK` 确认、`XAUTOCLAIM` 崩溃补偿。
- `RetryScheduler`：SQLite 重试表与 Redis ZSET + Lua 原子弹出两种实现，不引入 Redisson。
- `EventBus`：SQLite 读模型（SSE 继续用）与 Redis 事件流副本双写。

## 2. 状态机

```text
PENDING -> ROUTING -> RUNNING -> SUCCEEDED
                  |        |
                  +-> WAITING_DEPENDENCY / RETRYING -> RUNNING
                  +-> FAILED / CANCELLED
```

所有状态迁移均通过 `store.transition_task` 原子认领，任务带 `version` 与 `attempt_count`，
重复 Worker 认领会因期望状态不匹配而直接返回，避免双跑。

## 3. 重试与恢复

- 失败次数达到 `ORCHESTRA_RETRY_MAX_ATTEMPTS` 前进入 `RETRYING`，按指数退避 + 抖动计算延迟。
- 失败原因、尝试次数、下次重试时间写回 SQLite 投影，可通过任务接口查询。
- 应用启动时扫描未完成任务：`PENDING/ROUTING/RUNNING` 自动续跑，`RETRYING` 到期后由调度器拉起。
- Redis 模式下 Worker 崩溃后未 ACK 消息由 `XAUTOCLAIM` 认领补偿。

## 4. 使用方式

```text
# 默认 SQLite 工作流（无需 Redis）
ORCHESTRA_WORKFLOW_DRIVER=sqlite

# 生产 Redis Streams 工作流
ORCHESTRA_WORKFLOW_DRIVER=redis
ORCHESTRA_REDIS_URL=redis://127.0.0.1:6379/0
ORCHESTRA_REDIS_STREAM_PREFIX=orchestra
ORCHESTRA_REDIS_CONSUMER_GROUP=orchestra-workers
```

Redis 连接失败时应用自动回退到 SQLite 驱动，本地开发不会被外部依赖阻塞。

## 5. 验证记录

- `python -m unittest discover -s tests -v`：62 项通过。
- 包 3 新增 `tests/test_workflow.py` 覆盖状态机、RetryPolicy、SQLite 重试/恢复、Redis ZSET 延迟队列、
  Redis Stream 事件总线与 Worker 命令消费闭环。
