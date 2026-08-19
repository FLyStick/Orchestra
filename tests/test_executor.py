import asyncio
import tempfile
import unittest
from pathlib import Path

from orchestra.contracts.task import TaskInput, TaskStatus
from orchestra.executor import Executor
from orchestra.llm import LLMResult, LLMService, MockLLMProvider
from orchestra.router import RuleRouter
from orchestra.store import SQLiteStore


class ExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = SQLiteStore(self.root / "test.db")
        llm_service = LLMService(MockLLMProvider(), "mock-model")
        self.executor = Executor(
            store=self.store,
            llm_service=llm_service,
            router=RuleRouter(),
            workspace_root=self.root / "workspaces",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_simple_task_succeeds(self) -> None:
        task_input = TaskInput(query="报销标准是什么", session_id="session-1")
        output = asyncio.run(self.executor.execute_sync(task_input))
        self.assertEqual(output["status"], TaskStatus.SUCCEEDED.value)
        self.assertEqual(output["strategy"], "simple")
        self.assertIn("[Mock]", output["result"])
        self.assertGreater(output["token_usage"]["input_tokens"], 0)

    def test_dag_task_runs_subtasks(self) -> None:
        task_input = TaskInput(
            query="分析合同付款风险然后生成合规清单",
            session_id="session-2",
        )
        output = asyncio.run(self.executor.execute_sync(task_input))
        self.assertEqual(output["status"], TaskStatus.SUCCEEDED.value)
        self.assertEqual(output["strategy"], "dag")
        subtask_file = self.root / "workspaces" / "session-2" / "subtasks" / "t1.md"
        self.assertTrue(subtask_file.exists())
        events = self.store.list_events(output["task_id"])
        event_types = [event["event_type"] for event in events]
        self.assertIn("task.completed", event_types)

    def test_react_task_runs_via_executor(self) -> None:
        class ReactProvider:
            calls = 0

            async def complete(
                self,
                messages: list[dict[str, str]],
                model: str | None = None,
                max_tokens: int | None = None,
            ) -> LLMResult:
                ReactProvider.calls += 1
                if ReactProvider.calls == 1:
                    text = '{"tool": "rag_search", "arguments": {"query": "报销标准"}}'
                else:
                    text = "已完成"
                return LLMResult(text=text, input_tokens=10, output_tokens=5, model=model or "fake")

        llm_service = LLMService(ReactProvider(), "default")
        executor = Executor(
            store=self.store,
            llm_service=llm_service,
            router=RuleRouter(),
            workspace_root=self.root / "workspaces",
        )
        output = asyncio.run(
            executor.execute_sync(
                TaskInput(
                    query="请调用rag_search查询报销标准",
                    session_id="session-react",
                    strategy="react",
                )
            )
        )
        self.assertEqual(output["status"], TaskStatus.SUCCEEDED.value)
        self.assertEqual(output["strategy"], "react")
        self.assertEqual(output["result"], "已完成")
        answer_file = self.root / "workspaces" / "session-react" / "answer.md"
        self.assertTrue(answer_file.exists())
        events = self.store.list_events(output["task_id"])
        event_types = [event["event_type"] for event in events]
        self.assertIn("tool.called", event_types)
        self.assertIn("tool.completed", event_types)


    def test_hr_simple_task_uses_rag_tool(self) -> None:
        output = asyncio.run(
            self.executor.execute_sync(
                TaskInput(
                    query="公司年假有几天",
                    session_id="session-hr",
                    context={"department": "hr"},
                )
            )
        )
        self.assertEqual(output["status"], TaskStatus.SUCCEEDED.value)
        self.assertEqual(output["strategy"], "simple")
        self.assertIn("已根据 rag_search", output["result"])
        rag_file = self.root / "workspaces" / "session-hr" / "rag" / "hr" / "leave-policy.md"
        self.assertTrue(rag_file.exists())
        events = self.store.list_events(output["task_id"])
        event_types = [event["event_type"] for event in events]
        self.assertIn("tool.called", event_types)
        self.assertIn("tool.completed", event_types)

if __name__ == "__main__":
    unittest.main()
