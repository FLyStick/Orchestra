"""拆解规划与校验单元测试。"""
import asyncio
import json
import unittest

from orchestra.contracts.subtask import SubtaskSpec
from orchestra.contracts.task import TaskInput
from orchestra.llm import LLMResult, LLMService
from orchestra.planning import (
    DecompositionPlan,
    DecompositionPlanner,
    PlanValidator,
    split_parts,
)
from orchestra.scenarios import select_scenario


class PlanValidatorTest(unittest.TestCase):
    """计划校验器：接受合法计划，拒绝环、缺依赖、非法工具与非法角色。"""

    def setUp(self) -> None:
        self.validator = PlanValidator()

    def test_accepts_valid_chain_plan(self) -> None:
        plan = DecompositionPlan(
            subtasks=(
                SubtaskSpec(
                    id="t1",
                    goal="识别条款",
                    tools=("contract_context",),
                    agent_role="contract_analyst",
                ),
                SubtaskSpec(
                    id="t2",
                    goal="匹配规则",
                    dependencies=("t1",),
                    tools=("rag_search",),
                    strategy="react",
                    agent_role="risk_analyst",
                ),
                SubtaskSpec(id="t3", goal="生成清单", dependencies=("t2",), agent_role="reviewer"),
            )
        )
        result = self.validator.validate(plan)
        self.assertTrue(result.valid)
        self.assertEqual(result.depth, 1)

    def test_rejects_cycle(self) -> None:
        plan = DecompositionPlan(
            subtasks=(
                SubtaskSpec(id="t1", goal="a", dependencies=("t2",)),
                SubtaskSpec(id="t2", goal="b", dependencies=("t1",)),
            )
        )
        result = self.validator.validate(plan)
        self.assertFalse(result.valid)
        self.assertTrue(any("循环" in error for error in result.errors))

    def test_rejects_missing_dependency(self) -> None:
        plan = DecompositionPlan(
            subtasks=(SubtaskSpec(id="t1", goal="a", dependencies=("t9",)),)
        )
        result = self.validator.validate(plan)
        self.assertFalse(result.valid)
        self.assertTrue(any("依赖缺失" in error for error in result.errors))

    def test_rejects_invalid_tool_and_strategy(self) -> None:
        plan = DecompositionPlan(
            subtasks=(SubtaskSpec(id="t1", goal="a", tools=("db_query",), strategy="magic"),)
        )
        result = self.validator.validate(plan)
        self.assertFalse(result.valid)
        self.assertTrue(any("非法工具" in error for error in result.errors))
        self.assertTrue(any("非法节点策略" in error for error in result.errors))

    def test_rejects_invalid_role(self) -> None:
        plan = DecompositionPlan(
            subtasks=(SubtaskSpec(id="t1", goal="a", agent_role="hacker"),)
        )
        result = self.validator.validate(plan)
        self.assertFalse(result.valid)
        self.assertTrue(any("非法角色" in error for error in result.errors))

    def test_rejects_empty_plan(self) -> None:
        result = self.validator.validate(DecompositionPlan(subtasks=()))
        self.assertFalse(result.valid)


class DecompositionPlannerTest(unittest.TestCase):
    """规划器：场景模板优先，通用请求走规则拆解。"""


class JsonPlanProvider:
    """返回合法 JSON 拆解计划的 Mock Provider。"""

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        payload = json.dumps(
            {
                "subtasks": [
                    {
                        "id": "t1",
                        "goal": "识别合同条款",
                        "strategy": "direct",
                        "agent_role": "contract_analyst",
                        "tools": ["contract_context"],
                    },
                    {
                        "id": "t2",
                        "goal": "输出审查报告",
                        "dependencies": ["t1"],
                        "agent_role": "reviewer",
                    },
                ],
                "rationale": "mock llm plan",
            },
            ensure_ascii=False,
        )
        return LLMResult(
            text=payload,
            input_tokens=10,
            output_tokens=10,
            model=model or "fake",
        )


class GarbagePlanProvider:
    """返回非法文本，验证 LLM 规划失败后回退规则规划。"""

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        return LLMResult(
            text="无法拆解",
            input_tokens=5,
            output_tokens=5,
            model=model or "fake",
        )


    def test_scenario_template_plan(self) -> None:
        planner = DecompositionPlanner()
        task = TaskInput(
            query="分析合同付款风险然后生成合规清单",
            session_id="s1",
            context={"department": "risk"},
        )
        scenario = select_scenario(task)
        plan = planner.plan(task, scenario)
        self.assertEqual(plan.planner, "scenario")
        self.assertEqual([spec.id for spec in plan.subtasks], ["t1", "t2", "t3"])
        self.assertTrue(PlanValidator().validate(plan).valid)

    def test_rule_sequential_plan(self) -> None:
        planner = DecompositionPlanner()
        task = TaskInput(query="分析市场趋势并且生成行业报告然后整理摘要", session_id="s1")
        plan = planner.plan(task)
        self.assertEqual(plan.planner, "rule")
        self.assertEqual([spec.id for spec in plan.subtasks], ["t1", "t2", "t3"])
        self.assertEqual(plan.subtasks[1].dependencies, ("t1",))
        self.assertEqual(plan.subtasks[2].dependencies, ("t2",))

    def test_split_parts_limits_to_four(self) -> None:
        parts = split_parts("分别处理A并且处理B同时处理C还有处理D最后处理E")
        self.assertLessEqual(len(parts), 4)

    def test_llm_planner_valid_json(self) -> None:
        planner = DecompositionPlanner()
        task = TaskInput(query="分析合同条款并生成审查报告", session_id="s2")
        plan = asyncio.run(
            planner.plan_with_llm(task, LLMService(JsonPlanProvider(), "default"))
        )
        self.assertEqual(plan.planner, "llm")
        self.assertTrue(PlanValidator().validate(plan).valid)

    def test_llm_planner_falls_back_on_invalid_json(self) -> None:
        planner = DecompositionPlanner()
        task = TaskInput(query="分析合同条款并生成审查报告", session_id="s3")
        plan = asyncio.run(
            planner.plan_with_llm(task, LLMService(GarbagePlanProvider(), "default"))
        )
        self.assertEqual(plan.planner, "rule")
        self.assertTrue(PlanValidator().validate(plan).valid)


if __name__ == "__main__":
    unittest.main()
