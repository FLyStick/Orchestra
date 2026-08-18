from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
class TokenBudget:
    total_tokens: int = 100_000
    per_agent_tokens: int = 20_000
    allow_model_fallback: bool = True


@dataclass(frozen=True)
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