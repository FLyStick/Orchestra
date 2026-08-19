import asyncio
import json
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


class SequenceProvider:
    """按固定序列返回模型输出，用于验证 DAG 内 React 节点的工具循环。"""

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.index = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        text = self.replies[min(self.index, len(self.replies) - 1)]
        self.index += 1
        return LLMResult(
            text=text,
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


    def test_dag_react_node_reuses_loop_and_shares_budget(self) -> None:
        provider = SequenceProvider([
            "t1 条款识别完成",
            json.dumps({"tool": "rag_search", "arguments": {"query": "付款风险"}}),
            "t2 规则匹配完成",
            "t3 审查清单完成",
            "最终汇总答案",
        ])
        strategy = DAGStrategy(LLMService(provider, "default"))
        workspace = MemoryWorkspace("session-react-node")
        events: list[tuple[str, dict]] = []
        context = StrategyContext(
            task_id="task-react-node",
            query="风控条款审查",
            session_id="session-react-node",
            workspace=workspace,
            subtasks=(
                SubtaskSpec(id="t1", goal="识别合同条款", tools=("contract_context",), agent_role="contract_analyst"),
                SubtaskSpec(
                    id="t2",
                    goal="匹配风险规则",
                    dependencies=("t1",),
                    tools=("rag_search", "workspace_read"),
                    strategy="react",
                    agent_role="risk_analyst",
                ),
                SubtaskSpec(id="t3", goal="生成审查清单", dependencies=("t2",), agent_role="reviewer"),
            ),
            emit=lambda event_type, payload: events.append((event_type, payload)),
        )
        result = asyncio.run(strategy.execute(context))
        self.assertEqual(result.output, "最终汇总答案")
        self.assertEqual(result.token_usage["calls"], 5)
        self.assertEqual(len(result.tool_calls), 2)
        tool_payloads = [
            payload
            for event_type, payload in events
            if event_type == "tool.called" and payload.get("subtask_id") == "t2"
        ]
        self.assertEqual(len(tool_payloads), 1)
        self.assertEqual(tool_payloads[0]["tool"], "rag_search")
        completed = [
            payload
            for event_type, payload in events
            if event_type == "agent.completed" and payload.get("subtask_id") == "t2"
        ]
        self.assertEqual(len(completed), 1)
        token_events = [
            payload
            for event_type, payload in events
            if event_type == "token.updated" and payload.get("subtask_id") == "t2"
        ]
        self.assertTrue(token_events)
        # t2 的第一个 token 事件应已包含 t1 的累计用量，证明预算跨节点共享。
        self.assertGreaterEqual(token_events[0]["token_usage"]["calls"], 2)
        files = asyncio.run(workspace.list_files())
        self.assertIn("subtasks/t2.md", files)
        self.assertIn("dag/t2/react_trace.md", files)
        self.assertIn("dag/t2/step_01_rag_search.md", files)

    def test_nested_dag_node_runs_recursively(self) -> None:
        strategy = DAGStrategy(LLMService(EchoProvider(), "default"))
        workspace = MemoryWorkspace("session-nested")
        events: list[tuple[str, dict]] = []
        context = StrategyContext(
            task_id="task-nested",
            query="嵌套编排",
            session_id="session-nested",
            workspace=workspace,
            subtasks=(
                SubtaskSpec(
                    id="t1",
                    goal="外层任务",
                    strategy="dag",
                    metadata={
                        "subtasks": (
                            SubtaskSpec(id="n1", goal="嵌套步骤一"),
                            SubtaskSpec(id="n2", goal="嵌套步骤二", dependencies=("n1",)),
                        )
                    },
                ),
            ),
            emit=lambda event_type, payload: events.append((event_type, payload)),
        )
        result = asyncio.run(strategy.execute(context))
        self.assertTrue(result.output)
        self.assertEqual(result.token_usage["calls"], 3)
        files = asyncio.run(workspace.list_files())
        self.assertIn("subtasks/t1.md", files)
        self.assertIn("subtasks/n1.md", files)
        self.assertIn("subtasks/n2.md", files)
        started_ids = {
            payload.get("subtask_id")
            for event_type, payload in events
            if event_type == "agent.started"
        }
        self.assertIn("t1", started_ids)
        self.assertIn("n1", started_ids)
        self.assertIn("n2", started_ids)

if __name__ == "__main__":
    unittest.main()
