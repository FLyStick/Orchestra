"""Simple 升级闭环单元测试：低置信度与 RAG 失败都会升级 React 并推送事件。"""
import asyncio
import unittest

from orchestra.contracts.events import EventType
from orchestra.contracts.strategies import StrategyContext
from orchestra.llm import LLMResult, LLMService
from orchestra.strategies.simple import SimpleStrategy
from orchestra.tools import ToolRegistry, ToolResult
from orchestra.workspace.memory import MemoryWorkspace


class FinalAnswerProvider:
    """始终返回最终答案，配合验证升级到 React 后能正常收尾。"""

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        return LLMResult(
            text="最终答案",
            input_tokens=10,
            output_tokens=5,
            model=model or "fake",
        )


class FailingRagTool:
    """模拟 RAG 检索失败，触发 Simple -> React 升级。"""

    name = "rag_search"
    description = "failing rag"
    parameters: dict[str, object] = {}

    async def run(self, arguments: dict[str, object], context: object) -> ToolResult:
        return ToolResult(output="未检索到相关制度文档", success=False)


class SimpleEscalationTest(unittest.TestCase):
    def _context(self, workspace: MemoryWorkspace, events: list, **overrides) -> StrategyContext:
        context = {
            "routing_decision": {"confidence": 1.0, "strategy": "simple"},
            **overrides,
        }
        return StrategyContext(
            task_id="task-escalation",
            query="年假制度怎么规定的",
            session_id="session-escalation",
            workspace=workspace,
            context=context,
            emit=lambda event_type, payload: events.append((event_type, payload)),
        )

    def test_low_confidence_escalates_to_react_and_emits_event(self) -> None:
        strategy = SimpleStrategy(LLMService(FinalAnswerProvider(), "default"))
        workspace = MemoryWorkspace("session-escalation")
        events: list[tuple[str, dict]] = []
        context = self._context(
            workspace,
            events,
            routing_decision={"confidence": 0.4, "strategy": "simple"},
        )
        result = asyncio.run(strategy.execute(context))
        self.assertEqual(result.output, "最终答案")
        event_types = [event_type for event_type, _ in events]
        self.assertIn(EventType.ROUTING_ESCALATED.value, event_types)

    def test_rag_failure_escalates_to_react_with_event(self) -> None:
        registry = ToolRegistry()
        registry.register(FailingRagTool())
        strategy = SimpleStrategy(LLMService(FinalAnswerProvider(), "default"), registry)
        workspace = MemoryWorkspace("session-rag-fail")
        events: list[tuple[str, dict]] = []
        context = self._context(
            workspace,
            events,
            scenario_id="hr_policy_qa",
        )
        result = asyncio.run(strategy.execute(context))
        self.assertEqual(result.output, "最终答案")
        event_types = [event_type for event_type, _ in events]
        self.assertIn(EventType.ROUTING_ESCALATED.value, event_types)


if __name__ == "__main__":
    unittest.main()
