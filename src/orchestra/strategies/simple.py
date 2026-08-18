"""Simple 策略：单 Agent 一次 LLM 调用直接回答。"""
from __future__ import annotations

from ..budget import TokenBudgetTracker
from ..contracts.strategies import BaseStrategy, StrategyContext, StrategyResult, StrategyType
from ..llm import LLMService, estimate_tokens


class SimpleStrategy(BaseStrategy):
    def __init__(self, llm: LLMService) -> None:
        self._llm = llm

    @property
    def name(self) -> StrategyType:
        return StrategyType.SIMPLE

    async def execute(self, context: StrategyContext) -> StrategyResult:
        messages = [
            {"role": "system", "content": "你是企业内部多智能体编排框架中的通用助手。"},
            {"role": "user", "content": context.query},
        ]
        # 单次调用即可完成，不进行任务拆解，但受总预算与单 Agent 预算双重约束。
        tracker = TokenBudgetTracker(context.budget)
        input_estimate = estimate_tokens("".join(m.get("content", "") for m in messages))
        tracker.ensure_available(input_estimate)
        model = tracker.choose_model(self._llm.default_model, self._llm.fallback_model)
        result = await self._llm.complete(
            messages,
            max_tokens=tracker.next_max_tokens(input_estimate),
            model=model,
        )
        tracker.record(result.input_tokens, result.output_tokens)
        # 最终答案写入工作区，便于后续 Agent 复用。
        await context.workspace.write("answer.md", result.text)
        return StrategyResult(
            output=result.text,
            token_usage={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "model": result.model,
            },
        )
