"""包 3 Redis Streams 工作流驱动：命令流分发，事件流可观测。"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ..config import Settings
from ..contracts.task import TaskInput, TaskStatus
from ..contracts.workflow import WorkflowCommand, WorkflowCommandKind, WorkflowEventType
from ..executor import Executor
from ..store import SQLiteStore
from .driver import SqliteWorkflowDriver, WorkflowDriver
from .event_bus import CompositeEventBus, RedisStreamEventBus, SqliteEventBus
from .retry import RetryPolicy
from .retry_scheduler import RedisZsetRetryScheduler


class RedisStreamWorkflowDriver(WorkflowDriver):
    """Redis Stream 工作流驱动。

    职责：任务提交写入命令流，Worker 消费执行；SQLite 继续作为
    API/SSE/历史查询的读模型，Redis ZSET 负责延迟重试队列。
    """

    def __init__(
        self,
        store: SQLiteStore,
        executor: Executor,
        redis,
        settings: Settings,
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self._redis = redis
        # Stream 名称（带前缀隔离环境）
        self.commands_stream = f"{settings.redis_stream_prefix}:task:commands"
        self.events_stream = f"{settings.redis_stream_prefix}:task:events"
        self.retry_key = f"{settings.redis_stream_prefix}:task:retry"
        # 消费者组（支持多 Worker 水平扩展）
        self.consumer_group = settings.redis_consumer_group
        self.consumer_name = f"orchestra-worker-{uuid.uuid4().hex[:8]}"
        # 延迟重试调度器（Redis ZSET）
        self.retry_scheduler = RedisZsetRetryScheduler(redis, self.retry_key)
        # 事件同时写 SQLite（SSE 读模型）与 Redis 事件流（可观测副本）。
        self.event_bus = CompositeEventBus(
            SqliteEventBus(store),
            RedisStreamEventBus(redis, self.events_stream),
        )
        self._sqlite = SqliteWorkflowDriver(
            store,
            executor,
            event_bus=self.event_bus,
            retry_scheduler=self.retry_scheduler,
            retry_policy=retry_policy,
        )
        self._started = False

    async def start(self) -> None:
        """创建命令流/事件流与消费组，并恢复未完成任务。"""
        if self._started:                    # ① 幂等保护
            return
        for stream in (self.commands_stream, self.events_stream):   # ② 遍历两个流
            try:
                # 命令流组从头创建以保留历史；事件流只等待新事件。
                start_id = "0" if stream == self.commands_stream else "$"   # ③ 起始位置不同
                await self._redis.xgroup_create(
                    stream,
                    self.consumer_group,     # 消费者组名
                    id=start_id,
                    mkstream=True,           # 流不存在则自动创建
                )
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):   # ④ 组已存在则忽略
                    raise
        self._started = True                 # ⑤ 置标志
        await self.recover()                 # ⑥ 恢复未完成任务

    async def submit(self, task_input: TaskInput, *, start: bool = True) -> str:
        """创建 SQLite 投影并发布 SUBMIT 命令，Worker 消费后执行。"""
        task_id = self.executor.create_pending_task(task_input)   # ① 落 SQLite（投影）
        await self._xadd_command(WorkflowCommand(WorkflowCommandKind.SUBMIT, task_id))  # ② 发命令
        await self.event_bus.publish(
            task_id,
            WorkflowEventType.COMMAND_ACCEPTED.value,
            {"command": "submit"},
        )                                                          # ③ 发事件（双写）
        return task_id

    async def execute(self, task_id: str) -> dict[str, Any]:
        """由 Worker 调用，执行 SQLite 本地工作流核心逻辑。"""
        return await self._sqlite.execute(task_id)

    async def retry(self, task_id: str) -> bool:
        """把到期重试任务发布为 RETRY 命令。"""
        task = self.store.get_task(task_id)
        if not task:
            return False
        await self._xadd_command(WorkflowCommand(WorkflowCommandKind.RETRY, task_id))
        return True

    async def recover(self) -> int:
        """扫描 SQLite 投影并续跑未完成任务。"""
        return await self._sqlite.recover()

    async def finish(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        result: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        return await self._sqlite.finish(task_id, status=status, result=result, error=error)

    async def cancel(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        if not task or self.store.is_terminal(task["status"]):
            return False
        await self._xadd_command(WorkflowCommand(WorkflowCommandKind.CANCEL, task_id))
        await self.event_bus.publish(
            task_id,
            WorkflowEventType.COMMAND_ACCEPTED.value,
            {"command": "cancel"},
        )
        return True

    async def close(self) -> None:
        """关闭 Redis 连接；Worker 需先停止。"""
        close = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result):
            await result

    async def _xadd_command(self, command: WorkflowCommand) -> None:
        await self._redis.xadd(self.commands_stream, command.to_fields())   # 写入命令流
