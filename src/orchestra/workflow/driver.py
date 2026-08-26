"""包 3 工作流驱动：SQLite 本地驱动与统一抽象。"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

from ..contracts.events import EventType
from ..contracts.task import TaskInput, TaskStatus
from ..contracts.workflow import TaskExecutionError, WorkflowEventType
from ..executor import Executor
from ..store import SQLiteStore
from .event_bus import EventBus, SqliteEventBus
from .retry import RetryPolicy
from .retry_scheduler import RetryScheduler, SqliteRetryScheduler

logger = logging.getLogger(__name__)

# 可继续执行的任务状态；终态任务不允许再进入执行器。
_RUNNABLE = (
    TaskStatus.PENDING,
    TaskStatus.ROUTING,
    TaskStatus.RUNNING,
    TaskStatus.WAITING_DEPENDENCY,
    TaskStatus.RETRYING,
)


class WorkflowDriver(ABC):
    """包 3 工作流驱动统一接口。"""

    @abstractmethod
    async def submit(self, task_input: TaskInput, *, start: bool = True) -> str:
        """创建任务并进入工作流。"""

    @abstractmethod
    async def execute(self, task_id: str) -> dict[str, Any]:
        """执行单个任务并返回最新任务记录。"""

    @abstractmethod
    async def retry(self, task_id: str) -> bool:
        """触发一次重试，返回是否成功认领。"""

    @abstractmethod
    async def recover(self) -> int:
        """扫描未完成任务并续跑，返回恢复数量。"""

    @abstractmethod
    async def finish(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        result: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """将任务迁移到终态并发布事件。"""

    @abstractmethod
    async def cancel(self, task_id: str) -> bool:
        """取消未终止任务。"""

    async def start(self) -> None:
        """启动驱动（默认为空实现）。"""

    async def close(self) -> None:
        """关闭驱动释放资源（默认为空实现）。"""


class SqliteWorkflowDriver(WorkflowDriver):
    """SQLite 本地工作流驱动：进程内执行、指数退避重试与启动恢复。"""

    def __init__(
        self,
        store: SQLiteStore,
        executor: Executor,
        *,
        event_bus: EventBus | None = None,
        retry_scheduler: RetryScheduler | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.event_bus = event_bus or SqliteEventBus(store)
        self.retry_scheduler = retry_scheduler or SqliteRetryScheduler(store)
        self.retry_policy = retry_policy or RetryPolicy()
        self._background: set[asyncio.Task[Any]] = set()
        self._scheduler_task: asyncio.Task[Any] | None = None
        self._running = False

    async def start(self) -> None:
        """启动本地重试调度循环，并恢复上次未完成任务。"""
        if self._running:
            return
        self._running = True
        self._scheduler_task = asyncio.get_running_loop().create_task(self._scheduler_loop())
        await self.recover()

    async def close(self) -> None:
        """停止调度循环并取消未完成的后台任务。"""
        self._running = False
        tasks = list(self._background)
        if self._scheduler_task is not None:
            tasks.append(self._scheduler_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._background.clear()
        self._scheduler_task = None

    async def submit(self, task_input: TaskInput, *, start: bool = True) -> str:
        task_id = self.executor.create_pending_task(task_input)
        await self.event_bus.publish(
            task_id,
            WorkflowEventType.COMMAND_ACCEPTED.value,
            {"command": "submit"},
        )
        if start:
            self._launch(task_id)
        return task_id

    async def execute(self, task_id: str) -> dict[str, Any]:
        """执行单个任务并返回最新任务记录；由调度器或外部调用。"""
        task = self.store.get_task(task_id)
        if not task:
            return {}
        if self.store.is_terminal(task["status"]):
            return task
        status = TaskStatus(task["status"])
        if status not in _RUNNABLE:
            return task
        try:
            await self.executor.run(task_id, finalize_failure=False)
        except TaskExecutionError as exc:
            return await self._handle_attempt_failure(task_id, exc)
        except Exception as exc:
            return await self._handle_attempt_failure(
                task_id,
                TaskExecutionError(str(exc), stage="execution"),
            )
        return self.store.get_task(task_id) or {}

    async def _handle_attempt_failure(
        self,
        task_id: str,
        exc: TaskExecutionError,
    ) -> dict[str, Any]:
        """按重试策略决定进入 RETRYING 还是终态 FAILED。"""
        task = self.store.get_task(task_id) or {}
        current = TaskStatus(task.get("status", TaskStatus.PENDING.value))
        if current in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return task
        failed_count = int(task.get("attempt_count") or 0) + 1
        payload = {
            "task_id": task_id,
            "stage": exc.stage,
            "error": exc.message,
            "failed_attempt": failed_count,
            "max_attempts": self.retry_policy.max_attempts,
        }
        if self.retry_policy.should_retry(failed_count):
            delay_ms = self.retry_policy.next_delay_ms(failed_count - 1)
            due_at = datetime.now(timezone.utc) + timedelta(milliseconds=delay_ms)
            ok = self.store.transition_task(
                task_id,
                expected_statuses=(current,),
                status=TaskStatus.RETRYING,
                error=exc.message,
                duration_ms=exc.duration_ms,
                increment_attempt=True,
                next_retry_at=due_at.isoformat(),
            )
            if not ok:
                return self.store.get_task(task_id) or {}
            await self.retry_scheduler.schedule(task_id, max(0, int(delay_ms)))
            await self.event_bus.publish(
                task_id,
                WorkflowEventType.TASK_RETRY_SCHEDULED.value,
                {**payload, "delay_ms": int(delay_ms), "next_retry_at": due_at.isoformat()},
            )
            return self.store.get_task(task_id) or {}

        ok = self.store.transition_task(
            task_id,
            expected_statuses=(current,),
            status=TaskStatus.FAILED,
            error=exc.message,
            duration_ms=exc.duration_ms,
            increment_attempt=True,
        )
        if ok:
            await self.event_bus.publish(
                task_id,
                WorkflowEventType.RETRIES_EXHAUSTED.value,
                payload,
            )
            await self.event_bus.publish(
                task_id,
                EventType.TASK_FAILED.value,
                {"error": exc.message},
            )
        return self.store.get_task(task_id) or {}

    async def retry(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        if not task or TaskStatus(task["status"]) != TaskStatus.RETRYING:
            return False
        self._launch(task_id)
        return True

    async def recover(self) -> int:
        """扫描未完成任务并重新入队；RETRYING 任务由调度器到期拉起。"""
        rows = self.store.list_non_terminal_tasks()
        recovered = 0
        for row in rows:
            status = TaskStatus(row["status"])
            if status == TaskStatus.RETRYING:
                continue
            if status in _RUNNABLE:
                self._launch(row["task_id"])
                recovered += 1
        # 启动时同步拉起已到期的重试任务，避免等待调度循环首个 tick。
        for task_id in await self.retry_scheduler.pop_due(limit=200):
            task = self.store.get_task(task_id)
            if task and TaskStatus(task["status"]) == TaskStatus.RETRYING:
                self._launch(task_id)
                recovered += 1
        return recovered

    async def finish(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        result: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            return {}
        current = TaskStatus(task["status"])
        if current in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return task
        if status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ValueError(f"finish 只允许迁移到终态：{status.value}")
        ok = self.store.transition_task(
            task_id,
            expected_statuses=_RUNNABLE,
            status=status,
            result=result,
            error=error,
        )
        await self.retry_scheduler.cancel(task_id)
        if ok and status == TaskStatus.SUCCEEDED:
            await self.event_bus.publish(
                task_id,
                EventType.TASK_COMPLETED.value,
                {"status": "succeeded"},
            )
        elif ok and status == TaskStatus.FAILED:
            await self.event_bus.publish(
                task_id,
                EventType.TASK_FAILED.value,
                {"error": error},
            )
        return self.store.get_task(task_id) or {}

    async def cancel(self, task_id: str) -> bool:
        return self.executor.cancel(task_id)


    def _launch(self, task_id: str) -> None:
        """把任务放入事件循环后台执行，并保存引用防止被 GC。"""
        loop = asyncio.get_running_loop()
        background = loop.create_task(self.execute(task_id))
        self._background.add(background)
        background.add_done_callback(self._on_background_done)

    def _on_background_done(self, task: asyncio.Task[Any]) -> None:
        self._background.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("工作流后台任务异常", exc_info=task.exception())

    async def _scheduler_loop(self) -> None:
        """后台循环：定期扫描到期的 RETRYING 任务并拉起执行。"""
        while self._running:
            try:
                due = await self.retry_scheduler.pop_due(limit=100)
                for task_id in due:
                    task = self.store.get_task(task_id)
                    if task and TaskStatus(task["status"]) == TaskStatus.RETRYING:
                        self._launch(task_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SQLite 重试调度循环异常")
            await asyncio.sleep(0.5)
