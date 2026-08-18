import asyncio
import tempfile
import unittest
from pathlib import Path

from orchestra.contracts.task import TaskInput, TaskStatus
from orchestra.executor import Executor
from orchestra.llm import LLMService, MockLLMProvider
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


if __name__ == "__main__":
    unittest.main()