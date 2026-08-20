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
    """任务执行器：编排任务从提交到终态的完整生命周期。

    职责：创建任务 → 路由决策 → 选择策略与工作区 → 执行策略 →
    记录 Token 用量 → 更新状态并推送事件。所有状态变更都落库，
    保证重启后仍可查询任务历史。
    """

    def __init__(
        self,
        store: SQLiteStore,
        llm_service: LLMService,
        router: RuleRouter,
        workspace_root: Path,
    ) -> None:
        self.store = store  # 持久化存储（任务、事件、Token 用量）。
        self.llm_service = llm_service  # LLM 服务（含主/备模型切换）。
        self.router = router  # 规则路由器（复杂度评分 + 策略选择）。
        self.workspace_root = Path(workspace_root)  # 文件工作区根目录。
        # 预创建三种策略实例，按路由结果复用，避免每次执行重复初始化。
        self._simple = SimpleStrategy(llm_service)
        self._dag = DAGStrategy(llm_service)
        self._react = ReactStrategy(llm_service)
        # 后台任务集合：持有引用防止被 GC，完成后自动移除。
        self._tasks: set[asyncio.Task[Any]] = set()
        # 取消标记集合：记录被取消的任务 ID，供执行中检查。
        self._cancel_flags: set[str] = set()

    # 创建任务后立即返回；实际执行放在事件循环的后台任务中。
    def submit(self, task_input: TaskInput) -> str:
        """提交任务：落库 + 发创建事件，然后异步执行，立即返回 task_id。"""
        task_id = self.store.create_task(task_input)
        self.store.append_event(
            task_id,
            EventType.TASK_CREATED.value,
            {
                "session_id": task_input.session_id,
                "query_preview": task_input.query[:100],  # 只存前 100 字符预览。
            },
        )
        loop = asyncio.get_running_loop()
        background = loop.create_task(self.run(task_id))
        self._tasks.add(background)
        background.add_done_callback(self._tasks.discard)  # 完成后从集合移除。
        return task_id

    async def execute_sync(self, task_input: TaskInput) -> dict[str, Any]:
        """同步执行入口：直接等待任务完成（供测试或内部调用）。"""
        task_id = self.store.create_task(task_input)
        return await self.run(task_id)

    async def run(self, task_id: str) -> dict[str, Any]:
        """执行任务主流程：路由 → 执行 → 落库 → 发事件。

        返回任务的最新记录（含状态、结果、错误等）。
        """
        started = time.monotonic()  # 记录开始时间，用于计算耗时。
        task = self.store.get_task(task_id)
        if task is None:
            return {}
        task_input = self._restore_input(task)  # 从存储记录还原输入契约。
        self.store.update_task(task_id, status=TaskStatus.ROUTING)

        try:
            # 路由失败统一落 FAILED 并发出失败事件。
            decision = self.router.route(task_input)
        except ValueError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            self.store.update_task(task_id, status=TaskStatus.FAILED, error=str(exc), duration_ms=duration_ms)
            self.store.append_event(task_id, EventType.TASK_FAILED.value, {"error": str(exc)})
            return self.store.get_task(task_id) or {}

        # 路由完成后若已被取消，直接返回当前状态，不再继续执行。
        if self._is_cancelled(task_id):
            return self.store.get_task(task_id) or {}

        self.store.update_task(task_id, status=TaskStatus.RUNNING, strategy=decision.strategy.value)
        self.store.append_event(
            task_id,
            EventType.TASK_ROUTED.value,
            {
                "strategy": decision.strategy.value,
                "complexity_score": decision.complexity_score,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "reasons": list(decision.reasons),
                "features": decision.features.to_dict() if decision.features else None,
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

        # 合并路由决策附加的上下文（如场景 ID），供策略层使用。
        task_context = dict(task_input.context)
        if decision.scenario_id:
            task_context["scenario_id"] = decision.scenario_id
        # 路由决策的完整快照注入策略上下文，Simple 升级闭环据此判断低置信度。
        task_context["routing_decision"] = {
            "strategy": decision.strategy.value,
            "confidence": decision.confidence,
            "complexity_score": decision.complexity_score,
            "reasons": list(decision.reasons),
            "scenario_id": decision.scenario_id,
        }
        task_context["routing_escalation"] = "react"

        context = StrategyContext(
            task_id=task_id,
            query=task_input.query,
            session_id=task_input.session_id,
            workspace=workspace,
            budget=task_input.budget,
            context=task_context,
            max_iterations=task_input.max_iterations,
            subtasks=decision.subtasks,
            emit=emit,
        )
        # 按路由结果选择对应策略实例。
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
            # 执行异常：落 FAILED 并记录错误信息。
            duration_ms = int((time.monotonic() - started) * 1000)
            self.store.update_task(task_id, status=TaskStatus.FAILED, error=str(exc), duration_ms=duration_ms)
            self.store.append_event(task_id, EventType.TASK_FAILED.value, {"error": str(exc)})
        else:
            # 执行成功：记录 Token 用量、更新状态为 SUCCEEDED 并推送完成事件。
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
        """取消任务：仅对未终止任务生效，返回是否成功取消。"""
        task = self.store.get_task(task_id)
        if not task or self.store.is_terminal(task["status"]):
            return False  # 任务不存在或已到终态，无法取消。
        self._cancel_flags.add(task_id)
        self.store.update_task(task_id, status=TaskStatus.CANCELLED)
        self.store.append_event(task_id, EventType.TASK_CANCELLED.value, {})
        return True

    def _restore_input(self, task: dict[str, Any]) -> TaskInput:
        """从存储记录还原 TaskInput 契约（预算等嵌套对象需重建）。"""
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
        """检查任务是否已被取消。"""
        return task_id in self._cancel_flags
