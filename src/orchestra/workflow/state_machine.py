"""自研状态机：定义任务状态迁移规则，保证重复消费时幂等。"""
from __future__ import annotations

from ..contracts.task import TaskStatus

# 允许的状态迁移表；终态（succeeded/failed/cancelled）不可再流转。
_ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.ROUTING, TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.ROUTING: {TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.WAITING_DEPENDENCY, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.RETRYING, TaskStatus.WAITING_DEPENDENCY, TaskStatus.CANCELLED},
    TaskStatus.WAITING_DEPENDENCY: {TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.RETRYING: {TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}

_TERMINAL = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """判断状态迁移是否合法。"""
    return target in _ALLOWED_TRANSITIONS.get(current, set())


def transition(current: TaskStatus, target: TaskStatus) -> bool:
    """执行状态迁移校验，非法迁移直接抛错以便定位。"""
    if not can_transition(current, target):
        raise ValueError(f"非法状态迁移：{current.value} -> {target.value}")
    return True


def is_terminal(status: TaskStatus) -> bool:
    """判断任务是否到达终态。"""
    return status in _TERMINAL
