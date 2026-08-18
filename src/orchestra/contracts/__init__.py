"""P1 阶段定义的核心数据契约与策略接口。"""

from .events import EventType, TaskEvent
from .routing import RoutingDecision
from .strategies import BaseStrategy, StrategyContext, StrategyResult, StrategyType
from .subtask import SubtaskSpec
from .task import TaskInput, TaskOutput, TaskStatus, TokenBudget
from .workspace import Workspace, WorkspaceConfig

__all__ = [
    "BaseStrategy",
    "EventType",
    "RoutingDecision",
    "StrategyContext",
    "StrategyResult",
    "StrategyType",
    "SubtaskSpec",
    "TaskEvent",
    "TaskInput",
    "TaskOutput",
    "TaskStatus",
    "TokenBudget",
    "Workspace",
    "WorkspaceConfig",
]