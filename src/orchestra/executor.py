"""任务执行器：后台执行路由、策略、Token 记录与状态流转。"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from .contracts.events import EventType
from .contracts.strategies import StrategyContext, StrategyType
from .contracts.task import TaskInput, TaskStatus, TokenBudget
from .llm import LLMService
from .router import RuleRouter
from .store import SQLiteStore
from .strategies.dag import DAGStrategy
from .strategies.react import ReactStrategy
from .strategies.simple import SimpleStrategy
from .workspace.local_workspace import LocalWorkspace
from .workspace.memory import MemoryWorkspace


class Executor:
    def __init__(
        self,
        store: SQLiteStore,
        llm_service: LLMService,
        router: RuleRouter,
        workspace_root: Path,
    ) -> None:
        self.store = store
        self.llm_service = llm_service
        self.router = router
        self.workspace_root = Path(workspace_root)
        self._simple = SimpleStrategy(llm_service)
        self._dag = DAGStrategy(llm_service)
        self._react = ReactStrategy(llm_service)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._cancel_flags: set[str] = set()

    # 创建任务后立即返回；实际执行放在事件循环的后台任务中。
    def submit(self, task_input: TaskInput) -> str:
        task_id = self.store.create_task(task_input)
        self.store.append_event(
            task_id,
            EventType.TASK_CREATED.value,
            {
                "session_id": task_input.session_id,
                "query_preview": task_input.query[:100],
            },
        )
        loop = asyncio.get_running_loop()
        background = loop.create_task(self.run(task_id))
        self._tasks.add(background)
        background.add_done_callback(self._tasks.discard)
        return task_id

    async def execute_sync(self, task_input: TaskInput) -> dict[str, Any]:
        task_id = self.store.create_task(task_input)
        return await self.run(task_id)

    async def run(self, task_id: str) -> dict[str, Any]:
        started = time.monotonic()
        task = self.store.get_task(task_id)
        if task is None:
            return {}
        task_input = self._restore_input(task)
        self.store.update_task(task_id, status=TaskStatus.ROUTING)

        try:
            # 路由失败统一落 FAILED 并发出失败事件。
            decision = self.router.route(task_input)
        except ValueError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            self.store.update_task(task_id, status=TaskStatus.FAILED, error=str(exc), duration_ms=duration_ms)
            self.store.append_event(task_id, EventType.TASK_FAILED.value, {"error": str(exc)})
            return self.store.get_task(task_id) or {}

        if self._is_cancelled(task_id):
            return self.store.get_task(task_id) or {}

        self.store.update_task(task_id, status=TaskStatus.RUNNING, strategy=decision.strategy.value)
        self.store.append_event(
            task_id,
            EventType.TASK_ROUTED.value,
            {
                "strategy": decision.strategy.value,
                "complexity_score": decision.complexity_score,
                "reason": decision.reason,
            },
        )

        # 按配置选择文件或内存工作区，策略层只依赖 Workspace 接口。
        workspace = (
            LocalWorkspace(self.workspace_root, task_input.session_id)
            if task_input.workspace_enabled
            else MemoryWorkspace(task_input.session_id)
        )

        # 策略层通过 emit 回调推送 agent/tool/workspace/token 事件。
        def emit(event_type: str, payload: dict[str, Any]) -> None:
            self.store.append_event(task_id, event_type, payload)

        context = StrategyContext(
            task_id=task_id,
            query=task_input.query,
            session_id=task_input.session_id,
            workspace=workspace,
            budget=task_input.budget,
            context=task_input.context,
            max_iterations=task_input.max_iterations,
            subtasks=decision.subtasks,
            emit=emit,
        )
        if decision.strategy == StrategyType.SIMPLE:
            strategy = self._simple
        elif decision.strategy == StrategyType.DAG:
            strategy = self._dag
        elif decision.strategy == StrategyType.REACT:
            strategy = self._react
        else:
            # 未接入的策略统一视为路由错误。
            raise ValueError(f"unsupported strategy: {decision.strategy.value}")
        self.store.append_event(
            task_id,
            EventType.STRATEGY_STARTED.value,
            {"strategy": decision.strategy.value},
        )

        try:
            # 策略执行过程统一落库与发事件，避免执行黑盒。
            result = await strategy.execute(context)
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            self.store.update_task(task_id, status=TaskStatus.FAILED, error=str(exc), duration_ms=duration_ms)
            self.store.append_event(task_id, EventType.TASK_FAILED.value, {"error": str(exc)})
        else:
            duration_ms = int((time.monotonic() - started) * 1000)
            token_usage = result.token_usage
            self.store.record_token_usage(
                task_id,
                agent_id=decision.strategy.value,
                input_tokens=int(token_usage.get("input_tokens", 0)),
                output_tokens=int(token_usage.get("output_tokens", 0)),
                model=str(token_usage.get("model", "unknown")),
            )
            self.store.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED,
                result=result.output,
                duration_ms=duration_ms,
            )
            usage = self.store.aggregate_token_usage(task_id)
            self.store.append_event(task_id, EventType.TOKEN_UPDATED.value, {"token_usage": usage})
            self.store.append_event(
                task_id,
                EventType.TASK_COMPLETED.value,
                {"status": "succeeded", "token_usage": usage, "duration_ms": duration_ms},
            )

        return self.store.get_task(task_id) or {}

    # 取消仅对未终止任务生效；执行器在状态迁移前检查取消标记。
    def cancel(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        if not task or self.store.is_terminal(task["status"]):
            return False
        self._cancel_flags.add(task_id)
        self.store.update_task(task_id, status=TaskStatus.CANCELLED)
        self.store.append_event(task_id, EventType.TASK_CANCELLED.value, {})
        return True

    def _restore_input(self, task: dict[str, Any]) -> TaskInput:
        budget = None
        if task.get("budget"):
            budget = TokenBudget(**task["budget"])
        return TaskInput(
            query=task["query"],
            session_id=task["session_id"],
            user_id=task["user_id"],
            context=task.get("context") or {},
            strategy=task.get("strategy"),
            budget=budget,
            max_iterations=task.get("max_iterations") or 10,
            workspace_enabled=bool(task.get("workspace_enabled", True)),
            metadata=task.get("metadata") or {},
        )

    def _is_cancelled(self, task_id: str) -> bool:
        return task_id in self._cancel_flags
