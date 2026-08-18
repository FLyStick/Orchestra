"""Simple 策略：单 Agent 一次 LLM 调用直接回答。"""
from __future__ import annotations

from ..contracts.strategies import BaseStrategy, StrategyContext, StrategyResult, StrategyType
from ..llm import LLMService


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
        # 单次调用即可完成，不进行任务拆解。
        limit = (
            context.budget.per_agent_tokens
            if context.budget and context.budget.per_agent_tokens > 0
            else None
        )
        result = await self._llm.complete(messages, max_tokens=limit)
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