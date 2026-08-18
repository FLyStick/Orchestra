import unittest

from orchestra.budget import BudgetExceededError, TokenBudgetTracker
from orchestra.contracts.task import TokenBudget


class TokenBudgetTrackerTest(unittest.TestCase):
    def test_next_max_tokens_respects_total_and_per_agent(self):
        tracker = TokenBudgetTracker(TokenBudget(total_tokens=120, per_agent_tokens=50))
        self.assertEqual(tracker.next_max_tokens(20), 50)
        tracker.record(30, 20)
        self.assertEqual(tracker.next_max_tokens(10), 50)
        tracker.record(60, 10)
        self.assertEqual(tracker.next_max_tokens(1), None)

    def test_choose_model_uses_fallback_near_budget_limit(self):
        tracker = TokenBudgetTracker(
            TokenBudget(total_tokens=1000, per_agent_tokens=100, allow_model_fallback=True)
        )
        self.assertEqual(tracker.choose_model("fast", "cheap"), "fast")
        tracker.record(800, 0)
        self.assertEqual(tracker.choose_model("fast", "cheap"), "cheap")

    def test_ensure_available_raises_when_exhausted(self):
        tracker = TokenBudgetTracker(TokenBudget(total_tokens=10, per_agent_tokens=5))
        with self.assertRaises(BudgetExceededError):
            tracker.ensure_available(10)


if __name__ == "__main__":
    unittest.main()
