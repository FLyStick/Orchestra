import unittest

from orchestra.contracts.strategies import StrategyType
from orchestra.contracts.task import TaskInput
from orchestra.router import RuleRouter


class ScenarioRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = RuleRouter()

    def test_hr_department_simple_question_routes_to_simple_rag(self) -> None:
        decision = self.router.route(
            TaskInput(query="年假怎么申请", session_id="s1", context={"department": "hr"})
        )
        self.assertEqual(decision.strategy, StrategyType.SIMPLE)
        self.assertEqual(decision.scenario_id, "hr_policy_qa")

    def test_hr_keyword_simple_question_routes_to_simple_rag(self) -> None:
        decision = self.router.route(
            TaskInput(query="公司年假制度怎么规定的", session_id="s1")
        )
        self.assertEqual(decision.strategy, StrategyType.SIMPLE)
        self.assertEqual(decision.scenario_id, "hr_policy_qa")

    def test_hr_complex_question_escalates_to_react(self) -> None:
        decision = self.router.route(
            TaskInput(
                query="比较年假婚假产假制度并且分析差异然后调用rag_search核实",
                session_id="s1",
                context={"department": "hr"},
            )
        )
        self.assertEqual(decision.strategy, StrategyType.REACT)
        self.assertEqual(decision.scenario_id, "hr_policy_qa")

    def test_risk_query_routes_to_dag_with_react_node(self) -> None:
        decision = self.router.route(
            TaskInput(query="分析合同付款风险然后生成合规清单", session_id="s1")
        )
        self.assertEqual(decision.strategy, StrategyType.DAG)
        self.assertEqual(decision.scenario_id, "risk_contract_review")
        ids = [spec.id for spec in decision.subtasks]
        self.assertEqual(ids, ["t1", "t2", "t3"])
        self.assertEqual(decision.subtasks[0].tools, ("contract_context",))
        self.assertEqual(decision.subtasks[0].strategy, "direct")
        self.assertEqual(decision.subtasks[1].tools, ("rag_search", "workspace_read"))
        self.assertEqual(decision.subtasks[1].strategy, "react")
        self.assertEqual(decision.subtasks[1].dependencies, ("t1",))
        self.assertEqual(decision.subtasks[2].dependencies, ("t2",))

    def test_risk_department_takes_precedence(self) -> None:
        decision = self.router.route(
            TaskInput(query="年假制度", session_id="s1", context={"department": "risk"})
        )
        self.assertEqual(decision.scenario_id, "risk_contract_review")


if __name__ == "__main__":
    unittest.main()

