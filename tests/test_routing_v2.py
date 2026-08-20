"""路由 v2 单元测试：ScorerV2、特征、置信度与计划校验入口。"""
import unittest

from orchestra.contracts.routing import RoutingFeatures
from orchestra.contracts.strategies import StrategyType
from orchestra.contracts.task import TaskInput
from orchestra.router import (
    RuleRouter,
    ScoredRouting,
    ScorerV2,
    ComplexityScorer,
)


class LowConfidenceScorer(ScorerV2):
    """固定返回低置信度的评分器，用于验证简单策略的升级标记。"""

    def evaluate(self, query: str, context: dict | None = None) -> ScoredRouting:
        return ScoredRouting(
            score=0.1,
            confidence=0.4,
            features=RoutingFeatures(text_length=1),
            reasons=("low_confidence",),
        )


class ScorerV2Test(unittest.TestCase):
    def test_evaluate_returns_structured_score(self) -> None:
        scorer = ScorerV2(ambiguous_band=(0.25, 0.35))
        scored = scorer.evaluate("分析合同风险并且生成审查清单", {"department": "risk"})
        self.assertGreaterEqual(scored.score, 0.3)
        self.assertTrue(scored.features.to_dict()["has_department"])
        self.assertTrue(any("score=" in reason for reason in scored.reasons))

    def test_ambiguous_band_lowers_confidence(self) -> None:
        scorer = ScorerV2(ambiguous_band=(0.3, 0.4))
        scored = scorer.evaluate("分析情况并且生成报告", {"department": "finance"})
        self.assertEqual(scored.score, 0.36)
        self.assertLess(scored.confidence, 0.5)

    def test_complexity_scorer_alias_keeps_score_method(self) -> None:
        scorer = ComplexityScorer()
        self.assertIsInstance(scorer.score("分析情况并且生成报告"), float)


class RuleRouterV2Test(unittest.TestCase):
    def test_decision_carries_features_and_confidence(self) -> None:
        router = RuleRouter()
        decision = router.route(TaskInput(query="报销标准是什么", session_id="s1"))
        self.assertEqual(decision.strategy, StrategyType.SIMPLE)
        self.assertIsNotNone(decision.features)
        self.assertGreaterEqual(decision.confidence, 0.0)
        self.assertTrue(decision.reasons)

    def test_explicit_dag_uses_planner_and_validator(self) -> None:
        router = RuleRouter()
        decision = router.route(TaskInput(query="你好", session_id="s1", strategy="dag"))
        self.assertEqual(decision.strategy, StrategyType.DAG)
        self.assertEqual([spec.id for spec in decision.subtasks], ["t1"])

    def test_scenario_threshold_override(self) -> None:
        router = RuleRouter(scenario_thresholds={"finance_policy_qa": 0.1})
        decision = router.route(TaskInput(query="报销标准是什么", session_id="s1"))
        self.assertEqual(decision.scenario_id, "finance_policy_qa")
        self.assertEqual(decision.strategy, StrategyType.SIMPLE)

    def test_low_confidence_simple_marks_escalation(self) -> None:
        router = RuleRouter(scorer=LowConfidenceScorer())
        decision = router.route(TaskInput(query="你好", session_id="s1"))
        self.assertEqual(decision.strategy, StrategyType.SIMPLE)
        self.assertLess(decision.confidence, 0.5)
        self.assertTrue(any("escalation" in reason for reason in decision.reasons))


if __name__ == "__main__":
    unittest.main()
