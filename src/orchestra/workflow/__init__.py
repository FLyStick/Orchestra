"""包 3：Redis Streams + 自研状态机工作流引擎。"""

from .driver import SqliteWorkflowDriver, WorkflowDriver

__all__ = [
    "SqliteWorkflowDriver",
    "WorkflowDriver",
    "RedisStreamWorkflowDriver",
    "RedisWorkflowWorker",
]


def __getattr__(name: str):
    """延迟加载 Redis 相关类，避免未安装 redis 时影响 SQLite 模式。"""
    if name == "RedisStreamWorkflowDriver":
        from .redis_driver import RedisStreamWorkflowDriver

        return RedisStreamWorkflowDriver
    if name == "RedisWorkflowWorker":
        from .worker import RedisWorkflowWorker

        return RedisWorkflowWorker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
