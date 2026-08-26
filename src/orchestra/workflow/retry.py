"""指数退避 + 随机抖动重试策略。"""
from __future__ import annotations

import random


class RetryPolicy:
    """计算重试次数与延迟，避免雪崩并满足可配置验收。"""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay_ms: int = 1000,
        max_delay_ms: int = 60000,
        jitter_ms: int = 200,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay_ms = max(0, int(base_delay_ms))
        self.max_delay_ms = max(self.base_delay_ms, int(max_delay_ms))
        self.jitter_ms = max(0, int(jitter_ms))

    def should_retry(self, failed_attempt_count: int) -> bool:
        """failed_attempt_count 是已失败次数，未达到上限前允许继续。"""
        return int(failed_attempt_count) < self.max_attempts

    def delay_for_attempt(self, attempt: int) -> int:
        """第 N 次重试的退避延迟；attempt 从 1 开始计数。"""
        attempt = max(1, int(attempt))
        exponent = max(0, attempt - 1)
        base = min(self.base_delay_ms * (2 ** exponent), self.max_delay_ms)
        jitter = 0
        if self.jitter_ms > 0:
            jitter = random.randint(-self.jitter_ms, self.jitter_ms)
        return max(0, base + jitter)

    def next_delay_ms(self, failed_attempt_count: int) -> int:
        """根据已失败次数返回下一次重试延迟。"""
        return self.delay_for_attempt(int(failed_attempt_count) + 1)
