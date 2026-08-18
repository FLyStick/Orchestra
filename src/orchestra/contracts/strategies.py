"""策略执行契约：StrategyType、上下文与结果。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .subtask import SubtaskSpec
from .task import TokenBudget
from .workspace import Workspace


# 策略标识与 API 的 strategy 字段对齐。
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
    token_usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
# 上下文携带查询、工作区、预算与子任务，策略通过它执行。
class StrategyContext:
    task_id: str
    query: str
    session_id: str
    workspace: Workspace
    budget: TokenBudget | None = None
    context: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 10
    subtasks: tuple[SubtaskSpec, ...] = ()


class BaseStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> StrategyType:
        """策略标识，供 Router 选择。"""

    @abstractmethod
    async def execute(self, context: StrategyContext) -> StrategyResult:
        """执行策略并返回结构化结果。"""