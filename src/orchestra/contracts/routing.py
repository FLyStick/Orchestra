"""路由结果契约：策略、复杂度、子任务与预算一次下发。"""
from __future__ import annotations

from dataclasses import dataclass

from .strategies import StrategyType
from .subtask import SubtaskSpec
from .task import TokenBudget

__all__ = ["RoutingDecision", "SubtaskSpec"]


# 子任务与依赖关系由 Router 生成，DAG 策略按此调度。
@dataclass(frozen=True)
class RoutingDecision:
    strategy: StrategyType = StrategyType.SIMPLE
    complexity_score: float = 0.0
    reason: str = ""
    budget: TokenBudget | None = None
    subtasks: tuple[SubtaskSpec, ...] = ()
    scenario_id: str | None = None  # 命中的 P4 业务场景标识，用于执行上下文增强