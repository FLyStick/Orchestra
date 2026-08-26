"""延迟重试调度器：SQLite 与 Redis ZSET 两种实现，接口一致。"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ..store import SQLiteStore

# 原子弹出到期任务；Redis Streams 不原生支持延迟队列，用 ZSET 自研。
_POP_DUE_SCRIPT = """
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local members = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now, 'LIMIT', 0, limit)
if #members > 0 then
  redis.call('ZREM', KEYS[1], unpack(members))
end
return members
"""


def _now_ms() -> int:
    """当前 UTC 毫秒时间戳。"""
    return int(time.time() * 1000)


class RetryScheduler(ABC):
    """延迟重试队列接口：schedule / cancel / pop_due。"""

    @abstractmethod
    async def schedule(self, key: str, delay_ms: int) -> None:
        """登记一个延迟任务。"""

    @abstractmethod
    async def cancel(self, key: str) -> bool:
        """取消未到期的延迟任务。"""

    @abstractmethod
    async def pop_due(self, limit: int = 100) -> list[str]:
        """弹出已到期的任务主键列表。"""


class SqliteRetryScheduler(RetryScheduler):
    """SQLite 重试队列：本地开发/测试默认实现。"""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    async def schedule(self, key: str, delay_ms: int) -> None:
        self._store.retry_schedule(key, _now_ms() + int(delay_ms))

    async def cancel(self, key: str) -> bool:
        return self._store.retry_cancel(key)

    async def pop_due(self, limit: int = 100) -> list[str]:
        return self._store.retry_pop_due(limit=limit)


class RedisZsetRetryScheduler(RetryScheduler):
    """Redis ZSET 延迟队列：到期后原子弹出，替代 Java Redisson。"""

    def __init__(self, redis, key: str) -> None:
        self._redis = redis
        self._key = key
        self._script = None
        try:
            # 真实 Redis 使用 Lua 原子弹出；fakeredis 不支持时走降级路径。
            self._script = redis.register_script(_POP_DUE_SCRIPT)
        except Exception:
            self._script = None

    async def schedule(self, key: str, delay_ms: int) -> None:
        await self._redis.zadd(self._key, {key: _now_ms() + int(delay_ms)})

    async def cancel(self, key: str) -> bool:
        return bool(await self._redis.zrem(self._key, key))

    async def pop_due(self, limit: int = 100) -> list[str]:
        if self._script is not None:
            try:
                members = await self._script(keys=[self._key], args=[str(_now_ms()), str(int(limit))])
                return list(members)
            except Exception:
                pass
        try:
            # fakeredis 等测试环境不支持 Lua 脚本时退化为 ZRANGE + ZREM。
            members = await self._redis.zrangebyscore(
                self._key,
                min=0,
                max=_now_ms(),
                start=0,
                num=int(limit),
            )
            members = list(members)
            if members:
                await self._redis.zrem(self._key, *members)
            return members
        except Exception:
            # 降级路径也失败时返回空列表，避免阻塞调度循环。
            return []
