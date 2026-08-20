"""评测器冒烟测试：验证黄金用例集与 mock 评测流程可运行。"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from orchestra.config import Settings
from orchestra.evals import GOLDEN_CASES, evaluate_cases, evaluate_routing, _load_routing_cases


class EvalSmokeTest(unittest.TestCase):
    """评测器冒烟测试：不依赖真实 LLM，用 mock provider 快速验证链路。"""

    def test_golden_cases_has_30_hr_queries(self) -> None:
        """黄金用例集应恰好 30 条，且全部属于 hr 部门。"""
        self.assertEqual(len(GOLDEN_CASES), 30)
        self.assertTrue(all(case.department == "hr" for case in GOLDEN_CASES))

    def test_mock_eval_returns_report(self) -> None:
        """用 mock provider 跑前 4 条用例，应返回完整评测报告。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 使用临时目录隔离数据库与工作区，避免污染本地 data。
            root = Path(tmp)
            settings = Settings(
                llm_provider="mock",
                db_path=str(root / "eval.db"),
                workspace_root=str(root / "workspaces"),
            )
            report = asyncio.run(evaluate_cases(settings, limit=4))
            # 报告应包含 4 条用例、至少 2 条通过、有耗时与 Token 统计。
            self.assertEqual(report.total, 4)
            self.assertGreaterEqual(report.passed, 2)
            self.assertGreaterEqual(report.p95_latency_ms, 0)
            self.assertGreater(report.total_tokens, 0)
            # 目标通过率占位值应为 0.87（验收口径）。
            self.assertAlmostEqual(report.target_pass_rate, 0.87)

    def test_routing_golden_has_at_least_60_cases(self) -> None:
        """路由黄金集应达到包 1 验收门槛 60 条以上。"""
        cases = _load_routing_cases(Settings().routing_golden_file)
        self.assertGreaterEqual(len(cases), 60)

    def test_routing_eval_accuracy(self) -> None:
        """纯规则路由评测应达到验收占位准确率 90%。"""
        report = evaluate_routing()
        self.assertGreaterEqual(report.total, 60)
        self.assertGreaterEqual(report.accuracy, 0.9)


if __name__ == "__main__":
    unittest.main()
