"""Token 预算控制器：总额限制、单次调用限制与预算触发降级。

在策略执行期间实时累计 token 用量，提供三类能力：
1. 剩余额度查询（remaining）；
2. 按剩余额度动态收紧单次调用的 max_tokens（next_max_tokens）；
3. 预算接近耗尽时自动切换到备用模型（choose_model）。
"""
from __future__ import annotations

from .contracts.task import TokenBudget


class BudgetExceededError(RuntimeError):
    """预算耗尽时抛出，调用方可根据策略降级或终止。"""


class TokenBudgetTracker:
    """在策略执行期间累计 Token 用量，按总预算动态收紧单次调用上限。"""

    def __init__(self, budget: TokenBudget | None, fallback_threshold: float = 0.25) -> None:
        """初始化预算跟踪器。

        Args:
            budget: Token 预算配置；为 None 表示不限制。
            fallback_threshold: 剩余额度低于总预算该比例时切换备用模型（默认 25%）。
        """
        self.budget = budget
        self.fallback_threshold = fallback_threshold
        # 已累计的输入/输出 token 数。
        self._input_tokens = 0
        self._output_tokens = 0
        # 已发起的 LLM 调用次数。
        self.calls = 0

    @property
    def remaining(self) -> int:
        """剩余可用 token 额度。

        Returns:
            总预算减去已用 token；无预算时返回一个极大值（视为不限）。
        """
        if self.budget is None:
            return 10**9
        return max(0, self.budget.total_tokens - self._input_tokens - self._output_tokens)

    @property
    def usage(self) -> dict[str, int]:
        """返回当前累计用量统计。"""
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "calls": self.calls,
        }

    def next_max_tokens(self, estimated_input: int = 0) -> int | None:
        """计算下一次 LLM 调用的 max_tokens 上限。

        取"单 Agent 上限"与"剩余额度（扣除预估输入）"的较小值，
        保证总预算不被单次调用突破。

        Args:
            estimated_input: 预估的本次输入 token 数。

        Returns:
            本次调用的 max_tokens；无预算或额度不足时返回 None（不限制/不调用）。
        """
        if self.budget is None:
            return None
        per_agent = self.budget.per_agent_tokens or self.budget.total_tokens
        available = max(0, self.remaining - estimated_input)
        limit = min(per_agent, available)
        return limit if limit > 0 else None

    def choose_model(self, default_model: str, fallback_model: str | None) -> str | None:
        """根据剩余额度选择本次调用使用的模型。

        剩余额度低于阈值（总预算 * fallback_threshold）时切换到备用模型，
        用更便宜的模型完成收尾，避免预算超支。

        Args:
            default_model: 主模型名。
            fallback_model: 备用模型名；为 None 表示不降级。

        Returns:
            本次调用应使用的模型名；无预算时返回主模型。
        """
        if not fallback_model or self.budget is None:
            return default_model or None
        threshold = max(1, int(self.budget.total_tokens * self.fallback_threshold))
        if self.remaining <= threshold:
            return fallback_model
        return default_model or None

    def record(self, input_tokens: int, output_tokens: int) -> None:
        """记录一次 LLM 调用的实际用量。

        Args:
            input_tokens: 本次调用输入 token 数。
            output_tokens: 本次调用输出 token 数。
        """
        # 负数用量按 0 处理，防止异常数据污染统计。
        self._input_tokens += max(0, input_tokens)
        self._output_tokens += max(0, output_tokens)
        self.calls += 1

    def ensure_available(self, estimated_input: int = 0) -> None:
        """在调用前校验剩余额度是否足够。

        Args:
            estimated_input: 预估的本次输入 token 数。

        Raises:
            BudgetExceededError: 剩余额度不足以覆盖预估输入时抛出。
        """
        if self.budget is not None and self.remaining < estimated_input + 1:
            raise BudgetExceededError(
                f"budget exceeded: remaining={self.remaining}, estimated_input={estimated_input}"
            )
