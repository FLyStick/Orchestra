import asyncio
import unittest

from orchestra.contracts.events import EventType, TaskEvent
from orchestra.contracts.routing import RoutingDecision, SubtaskSpec
from orchestra.contracts.strategies import StrategyContext, StrategyType
from orchestra.contracts.task import TaskInput


class FakeWorkspace:
    def __init__(self) -> None:
        self.session_id = "session-1"
        self.files: dict[str, str] = {}

    async def read(self, path: str) -> str | None:
        return self.files.get(path)

    async def write(self, path: str, content: str) -> None:
        self.files[path] = content

    async def list_files(self) -> list[str]:
        return list(self.files)


class ContractSmokeTest(unittest.TestCase):
    def test_task_input_defaults(self) -> None:
        task = TaskInput(query="报销标准是什么", session_id="session-1")
        self.assertEqual(task.user_id, "anonymous")
        self.assertIsNone(task.budget)
        self.assertTrue(task.workspace_enabled)

    def test_routing_decision_holds_subtasks(self) -> None:
        decision = RoutingDecision(
            strategy=StrategyType.DAG,
            complexity_score=0.62,
            reason="多步骤查询",
            subtasks=(SubtaskSpec(id="t1", goal="第一步"),),
        )
        self.assertEqual(decision.strategy, StrategyType.DAG)
        self.assertEqual(len(decision.subtasks), 1)

    def test_strategy_context_uses_workspace(self) -> None:
        workspace = FakeWorkspace()
        context = StrategyContext(
            task_id="task-1",
            query="查询",
            session_id="session-1",
            workspace=workspace,
        )
        self.assertEqual(context.max_iterations, 10)

    def test_event_default_timestamp(self) -> None:
        event = TaskEvent(event_type=EventType.TASK_CREATED, task_id="task-1")
        self.assertIsNotNone(event.occurred_at)
        self.assertEqual(event.event_type, EventType.TASK_CREATED)

    def test_fake_workspace_round_trip(self) -> None:
        workspace = FakeWorkspace()
        result = asyncio.run(workspace.write("notes.md", "hello"))
        self.assertIsNone(result)
        self.assertEqual(asyncio.run(workspace.read("notes.md")), "hello")


if __name__ == "__main__":
    unittest.main()