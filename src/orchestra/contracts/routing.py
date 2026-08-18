from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .strategies import StrategyType
from .task import TokenBudget


@dataclass(frozen=True)
class SubtaskSpec:
    id: str
    goal: str
    dependencies: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    agent_role: str = "generalist"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    strategy: StrategyType = StrategyType.SIMPLE
    complexity_score: float = 0.0
    reason: str = ""
    budget: TokenBudget | None = None
    subtasks: tuple[SubtaskSpec, ...] = ()