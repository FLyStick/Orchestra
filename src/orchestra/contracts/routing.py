"""路由结果契约：策略、复杂度、特征、置信度、子任务与预算一次下发。"""
from __future__ import annotations

from dataclasses import dataclass

from .strategies import StrategyType
from .subtask import SubtaskSpec
from .task import TokenBudget

__all__ = ["RoutingDecision", "RoutingFeatures", "SubtaskSpec"]


@dataclass(frozen=True)
class RoutingFeatures:
    """路由特征：供评分器、置信度计算与 SSE 可观测性使用。"""

    text_length: int = 0
    clause_count: int = 1
    clause_hits: int = 0
    step_hits: int = 0
    tool_hits: int = 0
    react_hits: int = 0
    has_department: bool = False
    has_workspace_context: bool = False

    def to_dict(self) -> dict[str, object]:
        """转换为 JSON 友好的特征字典。"""
        return {
            "text_length": self.text_length,
            "clause_count": self.clause_count,
            "clause_hits": self.clause_hits,
            "step_hits": self.step_hits,
            "tool_hits": self.tool_hits,
            "react_hits": self.react_hits,
            "has_department": self.has_department,
            "has_workspace_context": self.has_workspace_context,
        }


# 子任务与依赖关系由 Router 生成，DAG 策略按此调度。
@dataclass(frozen=True)
class RoutingDecision:
    strategy: StrategyType = StrategyType.SIMPLE
    complexity_score: float = 0.0
    confidence: float = 1.0  # 路由置信度，0~1；低置信可触发复核/升级。
    reasons: tuple[str, ...] = ()  # 可解释的决策因子列表。
    reason: str = ""  # 兼容旧调用方的概要原因。
    features: RoutingFeatures | None = None  # 结构化特征，供评测与追踪。
    budget: TokenBudget | None = None
    subtasks: tuple[SubtaskSpec, ...] = ()
    scenario_id: str | None = None  # 命中的 P4/P5 业务场景标识，用于执行上下文增强
