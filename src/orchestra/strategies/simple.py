"""Simple 策略：单 Agent 一次 LLM 调用直接回答，RAG 场景前置检索，低置信/检索失败升级 React。"""
from __future__ import annotations

from ..budget import TokenBudgetTracker
from ..contracts.events import EventType
from ..contracts.strategies import (
    BaseStrategy,
    StrategyContext,
    StrategyResult,
    StrategyType,
)
from ..llm import LLMService, estimate_tokens
from ..tools import ToolRegistry, create_tool_registry
from .react import ReactStrategy

# 默认执行 RAG 前置检索的 Simple 场景。
_RAG_SCENARIOS = ("hr_policy_qa", "finance_policy_qa")
# 路由置信度低于该值时，Simple 直接升级为 React，保证歧义请求不丢质量。
_LOW_CONFIDENCE_THRESHOLD = 0.5


class SimpleStrategy(BaseStrategy):
    def __init__(self, llm: LLMService, registry: ToolRegistry | None = None) -> None:
        """初始化 Simple 策略。

        Args:
            llm: LLM 服务。
            registry: 工具注册表；不传则使用默认注册表。
        """
        self._llm = llm
        self._registry = registry or create_tool_registry()
        # 升级闭环复用 React 工具循环，与 Executor 中的 React 实例保持一致。
        self._react = ReactStrategy(llm, self._registry)

    @property
    def name(self) -> StrategyType:
        return StrategyType.SIMPLE

    def _emit(self, context: StrategyContext, event_type: str, payload: dict) -> None:
        """向外部推送事件（若上下文配置了事件回调）。"""
        if context.emit is not None:
            context.emit(event_type, payload)

    async def execute(self, context: StrategyContext) -> StrategyResult:
        """执行单次 LLM 回答；RAG 场景先检索文档，失败或低置信时升级 React。"""
        escalation_reason = self._escalation_reason(context)
        if escalation_reason:
            return await self._run_react(context, escalation_reason)

        messages = [
            {"role": "system", "content": "你是企业内部多智能体编排框架中的通用助手。"},
        ]
        # 单次调用即可完成，不进行任务拆解，但受总预算与单 Agent 预算双重约束。
        tracker = TokenBudgetTracker(context.budget)
        self._emit(context, EventType.AGENT_STARTED.value, {"model": self._llm.default_model})
        # HR/财务默认 Simple + RAG：先检索制度文档，再单次生成答案。
        scenario_id = context.context.get("scenario_id")
        if scenario_id in _RAG_SCENARIOS:
            tool = self._registry.get("rag_search")
            self._emit(
                context,
                EventType.TOOL_CALLED.value,
                {"tool": "rag_search", "arguments": {"query": context.query}},
            )
            if tool is None:
                rag_output = "RAG 工具不可用"
                success = False
            else:
                try:
                    tool_result = await tool.run({"query": context.query}, context)
                    rag_output = tool_result.output
                    success = tool_result.success
                except Exception as exc:
                    rag_output = f"RAG 工具执行异常：{exc}"
                    success = False
            self._emit(
                context,
                EventType.TOOL_COMPLETED.value,
                {"tool": "rag_search", "success": success},
            )
            # 检索失败或没有命中时升级为 React，让工具循环尝试换词或读取工作区。
            if not success or "未检索到" in rag_output:
                return await self._run_react(
                    context,
                    f"rag_search failed or empty: {rag_output[:80]}",
                )
            # 把检索结果作为上下文注入，保证回答有据可依。
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"请基于工具输出回答用户问题：\n工具输出(rag_search): {rag_output}\n"
                        f"问题：{context.query}"
                    ),
                },
            )
        else:
            messages.append({"role": "user", "content": context.query})
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
        self._emit(
            context,
            EventType.AGENT_COMPLETED.value,
            {"model": result.model},
        )
        return StrategyResult(
            output=result.text,
            token_usage={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "calls": 1,
                "model": result.model,
            },
        )

    def _escalation_reason(self, context: StrategyContext) -> str | None:
        """低置信度时返回升级原因；置信度正常时返回 None。"""
        routing = context.context.get("routing_decision") or {}
        confidence = routing.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < _LOW_CONFIDENCE_THRESHOLD:
            return (
                f"routing_confidence={confidence:.2f} < "
                f"{_LOW_CONFIDENCE_THRESHOLD:.2f}"
            )
        return None

    async def _run_react(self, context: StrategyContext, reason: str) -> StrategyResult:
        """升级前发出 SSE 可观测事件，再复用 ReactStrategy 重新执行。"""
        self._emit(
            context,
            EventType.ROUTING_ESCALATED.value,
            {
                "from_strategy": StrategyType.SIMPLE.value,
                "to_strategy": StrategyType.REACT.value,
                "reason": reason,
                "scenario_id": context.context.get("scenario_id"),
            },
        )
        return await self._react.execute(context)
