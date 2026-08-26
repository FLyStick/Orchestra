"""包 3 Redis 工作流 Worker：并发消费命令流、认领崩溃消息、轮询延迟队列。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..contracts.task import TaskStatus
from ..contracts.workflow import WorkflowCommand, WorkflowCommandKind
from .retry_scheduler import RetryScheduler

logger = logging.getLogger(__name__)


class RedisWorkflowWorker:
    """Redis Stream 消费者。

    使用 XREADGROUP 拉取命令流，XACK 确认成功；处理失败的消息保留在
    Pending Entries List 中，由 XAUTOCLAIM 补偿给其他 Worker。
    """

    def __init__(
        self,
        driver,
        *,
        redis,
        commands_stream: str,
        consumer_group: str,
        consumer_name: str,
        retry_scheduler: RetryScheduler,
        concurrency: int = 4,
        claim_idle_ms: int = 30_000,
        consume_block_ms: int = 5_000,
        poll_interval: float = 0.5,
    ) -> None:
        self._driver = driver
        self._redis = redis
        self._stream = commands_stream
        self._group = consumer_group
        self._consumer = consumer_name
        self._retry_scheduler = retry_scheduler
        self._concurrency = max(1, int(concurrency))
        self._claim_idle_ms = int(claim_idle_ms)
        self._block_ms = int(consume_block_ms)
        self._poll_interval = poll_interval
        self._running = False
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._jobs: set[asyncio.Task[Any]] = set()
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        """启动命令消费循环与重试调度轮询。"""
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._consume_loop(), name="redis-worker-consume"),
            asyncio.create_task(self._retry_loop(), name="redis-worker-retry"),
        ]

    async def stop(self) -> None:
        """停止循环并等待后台任务结束。"""
        self._running = False
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        for job in list(self._jobs):
            job.cancel()
        for job in list(self._jobs):
            try:
                await job
            except (asyncio.CancelledError, Exception):
                pass
        self._jobs.clear()

    async def _consume_loop(self) -> None:
        while self._running:
            try:
                response = await self._redis.xreadgroup(
                    self._group,
                    self._consumer,
                    {self._stream: ">"},
                    count=self._concurrency,
                    block=0,
                )
                if response:
                    for _stream, messages in response:
                        for message_id, fields in messages:
                            await self._dispatch(message_id, fields)
                else:
                    # fakeredis 对阻塞读实现不兼容，统一用短轮询避免阻塞事件循环。
                    await asyncio.sleep(0.05)
                await self._claim_stuck_messages()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Redis 命令消费循环异常")
                await asyncio.sleep(1)

    async def _dispatch(self, message_id: str, fields: dict[str, Any]) -> None:
        """按并发额度提交处理任务，处理完成后再确认消息。"""
        if not self._running:
            return
        await self._semaphore.acquire()
        job = asyncio.create_task(self._process(message_id, fields))
        self._jobs.add(job)
        job.add_done_callback(self._job_done)

    def _job_done(self, job: asyncio.Task[Any]) -> None:
        self._jobs.discard(job)
        self._semaphore.release()
        if not job.cancelled() and job.exception():
            logger.error("工作流命令处理异常", exc_info=job.exception())

    async def _process(self, message_id: str, fields: dict[str, Any]) -> None:
        try:
            command = WorkflowCommand.from_fields(fields)
            await self._handle(command)
            await self._redis.xack(self._stream, self._group, message_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 不 ACK，消息留在 Pending 列表中等待 XAUTOCLAIM 补偿。
            logger.warning("命令处理失败，消息 %s 留待重试：%s", message_id, exc)

    async def _handle(self, command: WorkflowCommand) -> None:
        if command.kind in {WorkflowCommandKind.SUBMIT, WorkflowCommandKind.RETRY}:
            await self._driver.execute(command.task_id)
        elif command.kind == WorkflowCommandKind.CANCEL:
            await self._driver.cancel(command.task_id)
        elif command.kind == WorkflowCommandKind.RECOVER:
            await self._driver.recover()

    async def _claim_stuck_messages(self) -> None:
        """认领超过空闲阈值的未 ACK 消息，模拟 Worker 崩溃后的补偿。"""
        try:
            result = await self._redis.xautoclaim(
                self._stream,
                self._group,
                self._consumer,
                self._claim_idle_ms,
                start_id="0-0",
                count=self._concurrency,
            )
            messages = self._extract_claimed_messages(result)
            for message_id, fields in messages:
                await self._dispatch(message_id, fields)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("XAUTOCLAIM 暂不可用：%s", exc)

    @staticmethod
    def _extract_claimed_messages(result: Any) -> list[tuple[str, dict[str, Any]]]:
        """兼容 redis-py 返回的三元组与 fakeredis 的简化列表。"""
        if isinstance(result, tuple):
            # redis-py: (next_start_id, messages, deleted_ids)
            if len(result) >= 2:
                result = result[1]
        return list(result or [])

    async def _retry_loop(self) -> None:
        while self._running:
            try:
                due = await self._retry_scheduler.pop_due(limit=self._concurrency * 10)
                for task_id in due:
                    task = self._driver.store.get_task(task_id)
                    if task and TaskStatus(task["status"]) == TaskStatus.RETRYING:
                        await self._driver.retry(task_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Redis 重试调度轮询异常")
            await asyncio.sleep(self._poll_interval)
