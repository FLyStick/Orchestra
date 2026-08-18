from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .task import TokenBudget
from .workspace import Workspace


class StrategyType(str, Enum):
    SIMPLE = "simple"
    DAG = "dag"
    REACT = "react"
    SWARM = "swarm"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyResult:
    output: Any = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass
class StrategyContext:
    task_id: str
    query: str
    session_id: str
    workspace: Workspace
    budget: TokenBudget | None = None
    context: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 10


class BaseStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> StrategyType:
        """策略标识，供 Router 选择。"""

    @abstractmethod
    async def execute(self, context: StrategyContext) -> StrategyResult:
        """执行策略并返回结构化结果。"""