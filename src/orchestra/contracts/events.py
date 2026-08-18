from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    TASK_CREATED = "task.created"
    TASK_ROUTED = "task.routed"
    STRATEGY_STARTED = "strategy.started"
    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    WORKSPACE_UPDATED = "workspace.updated"
    TOKEN_UPDATED = "token.updated"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"


@dataclass(frozen=True)
class TaskEvent:
    event_type: EventType
    task_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z")
    )