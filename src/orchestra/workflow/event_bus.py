"""事件总线：SQLite 读模型与 Redis Stream 发布两种实现。"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from ..store import SQLiteStore

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventBus(ABC):
    """工作流事件总线：publish / replay。"""

    @abstractmethod
    async def publish(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """发布一条工作流事件。"""

    @abstractmethod
    async def replay(self, task_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        """按增量 id 重放事件。"""


class SqliteEventBus(EventBus):
    """SQLite 事件总线：复用现有任务事件表，SSE 继续按 id 增量读取。"""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    async def publish(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self._store.append_event(task_id, event_type, payload)

    async def replay(self, task_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        return self._store.list_events(task_id, after_id)


class RedisStreamEventBus(EventBus):
    """Redis Stream 事件总线：发布到事件流，供额外可观测消费方使用。"""

    def __init__(self, redis, stream: str) -> None:
        self._redis = redis
        self._stream = stream

    async def publish(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        await self._redis.xadd(
            self._stream,
            {
                "task_id": task_id,
                "event_type": event_type,
                "payload": json.dumps(payload or {}, ensure_ascii=False),
                "occurred_at": _now_iso(),
            },
        )

    async def replay(self, task_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        entries = await self._redis.xrange(self._stream, min="0", max="+")
        parsed: list[dict[str, Any]] = []
        for message_id, fields in entries or []:
            if fields.get("task_id") != task_id:
                continue
            parsed.append(
                {
                    "id": message_id,
                    "task_id": task_id,
                    "event_type": fields.get("event_type", ""),
                    "payload": json.loads(fields.get("payload") or "{}"),
                    "occurred_at": fields.get("occurred_at", ""),
                }
            )
        return parsed
class CompositeEventBus(EventBus):
    """组合事件总线：SQLite 读模型优先，Redis Stream 作为可观测副本。"""

    def __init__(self, *buses: EventBus) -> None:
        self._buses = list(buses)

    async def publish(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """先写主总线，再尽力写入其余总线，不影响 SQLite 投影。"""
        if not self._buses:
            return
        await self._buses[0].publish(task_id, event_type, payload)
        # Redis 事件流不可用时只记录日志，不阻断任务状态流转。
        for bus in self._buses[1:]:
            try:
                await bus.publish(task_id, event_type, payload)
            except Exception:
                logger.warning("secondary event bus publish failed", exc_info=True)

    async def replay(self, task_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        """重放以第一个总线（SQLite）为准，保证 SSE id 连续。"""
        if not self._buses:
            return []
        return await self._buses[0].replay(task_id, after_id)
