import asyncio
import json
import unittest

from orchestra.contracts.strategies import StrategyContext
from orchestra.contracts.task import TokenBudget
from orchestra.llm import LLMResult, LLMService
from orchestra.strategies.react import ReactStrategy
from orchestra.workspace.memory import MemoryWorkspace


class SequenceProvider:
    def __init__(self, replies: list[str], model: str = "fake") -> None:
        self.replies = replies
        self.model = model
        self.index = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        text = self.replies[min(self.index, len(self.replies) - 1)]
        self.index += 1
        return LLMResult(text=text, input_tokens=12, output_tokens=8, model=model or self.model)


class HighUsageProvider:
    """第一轮消耗大量 Token，第二轮返回最终答案，用于验证预算触发降级。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        self.calls += 1
        if self.calls == 1:
            return LLMResult(
                text=json.dumps({"tool": "workspace_list", "arguments": {}}),
                input_tokens=800,
                output_tokens=0,
                model=model or "expensive",
            )
        return LLMResult(
            text="最终答案",
            input_tokens=10,
            output_tokens=5,
            model=model or "cheap",
        )


class ReactStrategyTest(unittest.TestCase):
    def test_react_calls_rag_tool_then_answers(self):
        provider = SequenceProvider([
            json.dumps({"tool": "rag_search", "arguments": {"query": "报销标准"}}),
            "最终答案",
        ])
        strategy = ReactStrategy(LLMService(provider, "default"))
        workspace = MemoryWorkspace("session-1")
        events: list[tuple[str, dict]] = []
        context = StrategyContext(
            task_id="task-1",
            query="请调用工具查询报销标准",
            session_id="session-1",
            workspace=workspace,
            emit=lambda event_type, payload: events.append((event_type, payload)),
        )
        result = asyncio.run(strategy.execute(context))
        self.assertEqual(result.output, "最终答案")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "rag_search")
        self.assertEqual(asyncio.run(workspace.read("answer.md")), "最终答案")
        files = asyncio.run(workspace.list_files())
        self.assertIn("react/step_01_rag_search.md", files)
        self.assertIn("rag/finance/expense-policy.md", files)
        event_types = [event_type for event_type, _ in events]
        self.assertIn("tool.called", event_types)
        self.assertIn("tool.completed", event_types)
        self.assertIn("workspace.updated", event_types)

    def test_react_uses_fallback_model_when_budget_low(self):
        provider = HighUsageProvider()
        strategy = ReactStrategy(LLMService(provider, "expensive", "cheap"))
        workspace = MemoryWorkspace("session-2")
        events: list[str] = []
        context = StrategyContext(
            task_id="task-2",
            query="回答",
            session_id="session-2",
            workspace=workspace,
            budget=TokenBudget(total_tokens=1000, per_agent_tokens=100, allow_model_fallback=True),
            emit=lambda event_type, payload: events.append(event_type),
        )
        result = asyncio.run(strategy.execute(context))
        self.assertEqual(result.token_usage["model"], "cheap")
        self.assertIn("budget.fallback", events)


if __name__ == "__main__":
    unittest.main()
