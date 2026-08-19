"""P4 预置业务场景：人事制度问答与风控条款审查。

场景配置同时用于路由决策和 API 场景清单：Router 根据部门上下文或
查询关键词命中场景后，直接下发策略与子任务（含工具和依赖关系）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts.strategies import StrategyType
from .contracts.subtask import SubtaskSpec
from .contracts.task import TaskInput


@dataclass(frozen=True)
class ScenarioConfig:
    """一个可复用的业务场景配置。"""

    scenario_id: str
    name: str
    department: str
    strategy: StrategyType
    tools: tuple[str, ...]
    description: str
    subtasks: tuple[SubtaskSpec, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """转换为 API 场景清单所需的 JSON 结构。"""
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "department": self.department,
            "strategy": self.strategy.value,
            "tools": list(self.tools),
            "description": self.description,
            "subtasks": [
                {
                    "id": spec.id,
                    "goal": spec.goal,
                    "dependencies": list(spec.dependencies),
                    "tools": list(spec.tools),
                    "agent_role": spec.agent_role,
                }
                for spec in self.subtasks
            ],
        }


def _build_risk_subtasks() -> tuple[SubtaskSpec, ...]:
    """风控条款审查的 DAG 三阶段子任务。"""
    return (
        SubtaskSpec(
            id="t1",
            goal=(
                "条款识别：提取合同中的付款、验收、违约金与争议解决条款，"
                "并摘录原文关键表述。"
            ),
            tools=("contract_context",),
            agent_role="contract_analyst",
            metadata={"tool_arguments": {"contract_context": {"contract_id": "demo"}}},
        ),
        SubtaskSpec(
            id="t2",
            goal=(
                "规则匹配：结合前置条款识别结果与内部风控规则，"
                "逐条判定风险点并给出依据。"
            ),
            dependencies=("t1",),
            tools=("rag_search",),
            agent_role="risk_analyst",
            metadata={
                "tool_arguments": {
                    "rag_search": {"query": "付款风险 验收风险 违约金风险 争议解决风险"}
                }
            },
        ),
        SubtaskSpec(
            id="t3",
            goal=(
                "审查清单生成：基于规则匹配结果输出合规审查清单，"
                "包含风险等级、判定依据与处置建议。"
            ),
            dependencies=("t2",),
            agent_role="reviewer",
        ),
    )


HR_SCENARIO = ScenarioConfig(
    scenario_id="hr_policy_qa",
    name="人事制度问答",
    department="人事",
    strategy=StrategyType.REACT,
    tools=("rag_search",),
    description="基于 RAG 检索与 ReAct 工具循环回答人事制度问题",
    subtasks=(),
)

RISK_SCENARIO = ScenarioConfig(
    scenario_id="risk_contract_review",
    name="风控条款审查",
    department="风控",
    strategy=StrategyType.DAG,
    tools=("contract_context", "rag_search"),
    description="合同条款识别、风险规则匹配与审查清单生成",
    subtasks=_build_risk_subtasks(),
)

ALL_SCENARIOS: tuple[ScenarioConfig, ...] = (HR_SCENARIO, RISK_SCENARIO)

_SCENARIO_BY_ID = {scenario.scenario_id: scenario for scenario in ALL_SCENARIOS}


def get_scenario(scenario_id: str) -> ScenarioConfig | None:
    """按场景标识查找场景配置，未命中时返回 None。"""
    return _SCENARIO_BY_ID.get(scenario_id)


# 部门别名与查询关键词：命中即进入对应业务场景。
_HR_DEPARTMENTS = ("hr", "人事", "人力", "员工关系")
_RISK_DEPARTMENTS = ("risk", "风控", "法务", "合规")
_HR_QUERY_MARKERS = (
    "制度", "规定", "政策", "年假", "转正", "试用期", "加班", "调休",
    "产假", "陪产假", "婚假", "丧假", "哺乳假", "社保", "公积金",
    "离职", "绩效", "培训报销",
)
_RISK_QUERY_MARKERS = (
    "风险", "审查", "条款", "违约金", "验收", "付款节点", "合规清单", "风控",
)


def select_scenario(task: TaskInput) -> ScenarioConfig | None:
    """根据部门上下文或查询关键词选择 P4 业务场景。"""
    department = str(task.context.get("department") or "").strip().lower()
    # 风控优先级高于人事，避免合同类问题被人事关键词误路由。
    if department in _RISK_DEPARTMENTS or any(
        marker in task.query for marker in _RISK_QUERY_MARKERS
    ):
        return RISK_SCENARIO
    if department in _HR_DEPARTMENTS or any(
        marker in task.query for marker in _HR_QUERY_MARKERS
    ):
        return HR_SCENARIO
    return None
