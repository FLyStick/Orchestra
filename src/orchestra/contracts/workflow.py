"""包 3 工作流命令/事件契约：Redis Streams 与 SQLite 统一数据格式。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkflowCommandKind(str, Enum):
    """工作流命令类型：任务提交、重试、取消与恢复。"""

    SUBMIT = "submit"
    RETRY = "retry"
    CANCEL = "cancel"
    RECOVER = "recover"


class WorkflowEventType(str, Enum):
    """工作流可观测事件类型：命令与任务状态生命周期。"""

    COMMAND_ACCEPTED = "workflow.command_accepted"
    COMMAND_COMPLETED = "workflow.command_completed"
    TASK_CLAIMED = "workflow.task_claimed"
    TASK_RETRY_SCHEDULED = "workflow.task_retry_scheduled"
    RETRIES_EXHAUSTED = "workflow.retries_exhausted"


class TaskExecutionError(Exception):
    """执行器抛给工作流驱动的一次性失败；由驱动决定重试或进入终态。"""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "execution",
        duration_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.duration_ms = duration_ms


@dataclass(frozen=True)
class WorkflowCommand:
    """Redis Stream 中传递的不可变命令。"""

    kind: WorkflowCommandKind
    task_id: str
    version: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def to_fields(self) -> dict[str, str]:
        """转换为 Redis Stream 的扁平字段。"""
        return {
            "kind": self.kind.value,
            "task_id": self.task_id,
            "version": str(self.version),
            "payload": json.dumps(self.payload, ensure_ascii=False),
        }

    @classmethod
    def from_fields(cls, fields: dict[str, Any]) -> "WorkflowCommand":
        """从 Redis Stream 字段还原为命令契约。"""
        return cls(
            kind=WorkflowCommandKind(str(fields.get("kind") or WorkflowCommandKind.SUBMIT.value)),
            task_id=str(fields.get("task_id") or ""),
            version=int(fields.get("version") or 0),
            payload=json.loads(fields.get("payload") or "{}"),
        )
