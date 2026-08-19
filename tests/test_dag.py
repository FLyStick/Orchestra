import asyncio
import unittest

from orchestra.contracts.strategies import StrategyContext
from orchestra.contracts.subtask import SubtaskSpec
from orchestra.llm import LLMResult, LLMService
from orchestra.strategies.dag import DAGStrategy
from orchestra.workspace.memory import MemoryWorkspace


class EchoProvider:
    """把最后一个 user 消息回显为子任务输出，便于验证 DAG 调度。"""

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        user_text = "".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
        return LLMResult(
            text=f"完成：{user_text[:40]}",
            input_tokens=12,
            output_tokens=8,
            model=model or "fake",
        )


class DagStrategyTest(unittest.TestCase):
    def test_dag_runs_tools_and_emits_agent_events(self) -> None:
        strategy = DAGStrategy(LLMService(EchoProvider(), "default"))
        workspace = MemoryWorkspace("session-dag")
        events: list[tuple[str, dict]] = []
        context = StrategyContext(
            task_id="task-dag",
            query="风控条款审查",
            session_id="session-dag",
            workspace=workspace,
            subtasks=(
                SubtaskSpec(
                    id="t1",
                    goal="识别合同条款",
                    tools=("contract_context",),
                    agent_role="contract_analyst",
                    metadata={"tool_arguments": {"contract_context": {"contract_id": "demo"}}},
                ),
                SubtaskSpec(
                    id="t2",
                    goal="匹配风险规则",
                    dependencies=("t1",),
                    tools=("rag_search",),
                    agent_role="risk_analyst",
                    metadata={"tool_arguments": {"rag_search": {"query": "付款风险 验收风险"}}},
                ),
                SubtaskSpec(
                    id="t3",
                    goal="生成审查清单",
                    dependencies=("t2",),
                    agent_role="reviewer",
                ),
            ),
            emit=lambda event_type, payload: events.append((event_type, payload)),
        )
        result = asyncio.run(strategy.execute(context))
        self.assertTrue(result.output)
        event_types = [event_type for event_type, _ in events]
        self.assertIn("agent.started", event_types)
        self.assertIn("agent.completed", event_types)
        self.assertIn("tool.called", event_types)
        self.assertIn("tool.completed", event_types)
        self.assertIn("workspace.updated", event_types)
        files = asyncio.run(workspace.list_files())
        self.assertIn("dag/t1/contract_context.md", files)
        self.assertIn("contracts/demo.md", files)
        self.assertIn("subtasks/t3.md", files)
        self.assertIn("answer.md", files)
        self.assertEqual(result.token_usage["calls"], 4)


if __name__ == "__main__":
    unittest.main()
