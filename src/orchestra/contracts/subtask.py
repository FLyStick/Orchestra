"""子任务描述，供 DAG 调度使用。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# dependencies 引用其他子任务 id，空元组表示无前置依赖。
@dataclass(frozen=True)
class SubtaskSpec:
    id: str
    goal: str #原始输入
    dependencies: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    agent_role: str = "generalist"
    metadata: dict[str, Any] = field(default_factory=dict) #附加元数据（如来源标记）