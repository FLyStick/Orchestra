"""任务输入输出与状态契约。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# 状态机与架构文档保持一致，终态为 succeeded/failed/cancelled。
class TaskStatus(str, Enum):
    PENDING = "pending"
    ROUTING = "routing"
    RUNNING = "running"
    WAITING_DEPENDENCY = "waiting_dependency"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
# 预算随任务下发，策略在执行中按该上限控制调用。
class TokenBudget:
    total_tokens: int = 100_000
    per_agent_tokens: int = 20_000
    allow_model_fallback: bool = True


@dataclass(frozen=True)
# 统一任务入口：API 与执行器共享同一份输入契约。
class TaskInput:
    query: str
    session_id: str
    user_id: str = "anonymous"
    context: dict[str, Any] = field(default_factory=dict)
    strategy: str | None = None
    budget: TokenBudget | None = None
    max_iterations: int = 10
    workspace_enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskOutput:
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    duration_ms: int | None = None
    created_at: str | None = None
    updated_at: str | None = None