"""任务输入输出与状态契约。

定义任务生命周期中的核心数据结构：状态枚举、输入、预算与输出，
API 层、执行器与存储层共享这些契约，保证各层数据格式一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# 状态机与架构文档保持一致，终态为 succeeded/failed/cancelled。
class TaskStatus(str, Enum):
    """任务状态枚举：描述任务从提交到终态的完整生命周期。

    非终态：pending → routing → running（可含 waiting_dependency / retrying）。
    终态：succeeded / failed / cancelled，到达终态后不再流转。
    """

    PENDING = "pending"  # 已提交，等待路由。
    ROUTING = "routing"  # 正在路由（复杂度评分、策略选择）。
    RUNNING = "running"  # 策略执行中。
    WAITING_DEPENDENCY = "waiting_dependency"  # 等待前置依赖完成（预留）。
    RETRYING = "retrying"  # 失败重试中（预留）。
    SUCCEEDED = "succeeded"  # 执行成功，result 已生成。
    FAILED = "failed"  # 执行失败，error 记录原因。
    CANCELLED = "cancelled"  # 被用户取消。


@dataclass(frozen=True)
# 预算随任务下发，策略在执行中按该上限控制调用。
class TokenBudget:
    total_tokens: int = 100_000
    per_agent_tokens: int = 20_000
    allow_model_fallback: bool = True


@dataclass(frozen=True)
# 统一任务入口：API 与执行器共享同一份输入契约。
class TaskInput:
    """任务输入契约：API 请求与执行器共享的统一入口。
    frozen=True 保证输入不可变，防止执行过程中被意外修改。
    """

    query: str  # 用户问题文本（必填）。
    session_id: str  # 会话标识，用于隔离工作区（必填）。
    user_id: str = "anonymous"  # 用户标识，默认匿名。
    context: dict[str, Any] = field(default_factory=dict)  # 附加上下文（如部门、权限）。
    strategy: str | None = None  # 显式指定策略；None 表示由路由器自动决策。
    budget: TokenBudget | None = None  # Token 预算；None 表示不限制。
    max_iterations: int = 10  # 最大迭代次数（React 循环等场景使用）。
    workspace_enabled: bool = True  # 是否启用文件工作区。
    metadata: dict[str, Any] = field(default_factory=dict)  # 附加元数据。


@dataclass
class TaskOutput:
    """任务输出契约：执行完成后返回给调用方的结构化结果。"""

    task_id: str  # 任务标识。
    status: TaskStatus = TaskStatus.PENDING  # 当前状态。
    result: Any = None  # 最终答案（成功时）。
    error: str | None = None  # 错误信息（失败时）。
    token_usage: dict[str, int] = field(default_factory=dict)  # token 用量统计。
    duration_ms: int | None = None  # 执行耗时（毫秒）。
    created_at: str | None = None  # 创建时间（ISO 格式）。
    updated_at: str | None = None  # 最后更新时间（ISO 格式）。