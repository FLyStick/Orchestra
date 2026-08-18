import unittest

from orchestra.contracts.strategies import StrategyType
from orchestra.contracts.task import TaskInput
from orchestra.router import RuleRouter


class RouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = RuleRouter()

    def test_simple_query_routes_to_simple(self) -> None:
        decision = self.router.route(TaskInput(query="公司年假几天", session_id="s1"))
        self.assertEqual(decision.strategy, StrategyType.SIMPLE)
        self.assertLess(decision.complexity_score, 0.3)

    def test_complex_query_routes_to_dag(self) -> None:
        decision = self.router.route(
            TaskInput(query="分析合同付款风险并且生成合规检查清单", session_id="s1")
        )
        self.assertEqual(decision.strategy, StrategyType.DAG)
        self.assertGreaterEqual(decision.complexity_score, 0.3)
        self.assertGreaterEqual(len(decision.subtasks), 2)

    def test_forced_strategy_is_respected(self) -> None:
        decision = self.router.route(
            TaskInput(query="你好", session_id="s1", strategy="dag")
        )
        self.assertEqual(decision.strategy, StrategyType.DAG)

    def test_invalid_strategy_raises(self) -> None:
        task = TaskInput(query="你好", session_id="s1", strategy="magic")
        with self.assertRaises(ValueError):
            self.router.route(task)


if __name__ == "__main__":
    unittest.main()