"""预置业务场景：人事、风控、财务、招采。

场景配置同时用于路由决策和 API 场景清单：Router 根据部门上下文或
查询关键词命中场景后，直接下发策略与子任务（含工具和依赖关系）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    metadata: dict[str, Any] = field(default_factory=dict)  # 场景级路由提示，如 simple_default。

    def to_dict(self) -> dict[str, object]:
        """转换为 API 场景清单所需的 JSON 结构。"""
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "department": self.department,
            "strategy": self.strategy.value,
            "tools": list(self.tools),
            "description": self.description,
            "metadata": dict(self.metadata),
            "subtasks": [
                {
                    "id": spec.id,
                    "goal": spec.goal,
                    "dependencies": list(spec.dependencies),
                    "tools": list(spec.tools),
                    "strategy": spec.strategy,
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
                "逐条判定风险点并给出依据；检索不足时主动调整检索词。"
            ),
            dependencies=("t1",),
            tools=("rag_search", "workspace_read"),
            strategy="react",
            agent_role="risk_analyst",
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


def _build_finance_review_subtasks() -> tuple[SubtaskSpec, ...]:
    """报销单据校验的 DAG 三阶段子任务。"""
    return (
        SubtaskSpec(
            id="t1",
            goal="单据字段提取：列出报销单中的发票、行程、审批等字段。",
            agent_role="finance_analyst",
        ),
        SubtaskSpec(
            id="t2",
            goal=("政策匹配：结合报销政策逐项校验字段，标注不满足项与依据。"),
            dependencies=("t1",),
            tools=("rag_search", "workspace_read"),
            strategy="react",
            agent_role="finance_analyst",
        ),
        SubtaskSpec(
            id="t3",
            goal="修改建议生成：输出不满足项与可执行的修改建议。",
            dependencies=("t2",),
            agent_role="reviewer",
        ),
    )


def _build_procurement_subtasks() -> tuple[SubtaskSpec, ...]:
    """招采流程指引的 DAG 子任务。"""
    return (
        SubtaskSpec(
            id="t1",
            goal="定位当前招采流程节点与项目状态。",
            agent_role="procurement_agent",
        ),
        SubtaskSpec(
            id="t2",
            goal="输出下一步操作、所需材料与注意事项。",
            dependencies=("t1",),
            agent_role="procurement_agent",
        ),
    )


HR_SCENARIO = ScenarioConfig(
    scenario_id="hr_policy_qa",
    name="人事制度问答",
    department="人事",
    strategy=StrategyType.REACT,
    tools=("rag_search",),
    description="基于 RAG 检索与 ReAct 工具循环回答人事制度问题",
    metadata={"simple_default": True, "escalation": "react"},
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

FINANCE_SCENARIO = ScenarioConfig(
    scenario_id="finance_policy_qa",
    name="财务报销咨询",
    department="财务",
    strategy=StrategyType.SIMPLE,
    tools=("rag_search",),
    description="财务报销政策问答，默认 Simple + RAG",
    metadata={"simple_default": True, "rag_required": True},
)

FINANCE_REVIEW_SCENARIO = ScenarioConfig(
    scenario_id="finance_invoice_review",
    name="报销单据校验",
    department="财务",
    strategy=StrategyType.DAG,
    tools=("rag_search", "workspace_read"),
    description="报销单据字段提取、政策匹配与修改建议",
    subtasks=_build_finance_review_subtasks(),
)

PROCUREMENT_SCENARIO = ScenarioConfig(
    scenario_id="procurement_process_qa",
    name="招采流程咨询",
    department="招采",
    strategy=StrategyType.SIMPLE,
    tools=(),
    description="招采流程节点与项目合同咨询",
    metadata={
        "simple_default": True,
        "dag_markers": ("流程", "节点", "下一步", "步骤", "招标", "供应商资质"),
    },
    subtasks=_build_procurement_subtasks(),
)

ALL_SCENARIOS: tuple[ScenarioConfig, ...] = (
    HR_SCENARIO,
    RISK_SCENARIO,
    FINANCE_SCENARIO,
    FINANCE_REVIEW_SCENARIO,
    PROCUREMENT_SCENARIO,
)

_SCENARIO_BY_ID = {scenario.scenario_id: scenario for scenario in ALL_SCENARIOS}


def get_scenario(scenario_id: str) -> ScenarioConfig | None:
    """按场景标识查找场景配置，未命中时返回 None。"""
    return _SCENARIO_BY_ID.get(scenario_id)


# 部门别名与查询关键词：命中即进入对应业务场景。
_HR_DEPARTMENTS = ("hr", "人事", "人力", "员工关系")
_RISK_DEPARTMENTS = ("risk", "风控", "法务", "合规")
_FINANCE_DEPARTMENTS = ("finance", "财务", "报销")
_PROCUREMENT_DEPARTMENTS = ("procurement", "招采", "采购", "供应商管理")
_HR_QUERY_MARKERS = (
    "制度", "规定", "政策", "年假", "转正", "试用期", "加班", "调休",
    "产假", "陪产假", "婚假", "丧假", "哺乳假", "社保", "公积金",
    "离职", "绩效",
)
_RISK_QUERY_MARKERS = (
    "风险", "审查", "条款", "违约金", "验收", "付款节点", "合规清单", "风控",
)
_FINANCE_QUERY_MARKERS = ("报销", "发票", "报销单", "差旅", "培训报销", "补贴", "单据")
_FINANCE_REVIEW_MARKERS = ("报销单", "发票", "单据", "校验", "不满足", "合规")
_PROCUREMENT_QUERY_MARKERS = ("招采", "采购", "供应商", "招标", "流程节点", "项目合同")


def select_scenario(task: TaskInput) -> ScenarioConfig | None:
    """根据部门上下文或查询关键词选择业务场景。

    优先级：风险关键词 > 财务部门上下文 > 其他部门上下文 >
    单据校验/财务/人事/招采关键词，避免重叠关键词误路由。
    """
    department = str(task.context.get("department") or "").strip().lower()
    # 风控优先级最高，合同风险问题保持原有 DAG 评审链路。
    if department in _RISK_DEPARTMENTS or any(
        marker in task.query for marker in _RISK_QUERY_MARKERS
    ):
        return RISK_SCENARIO
    # 财务部门默认走政策问答；出现单项校验关键词才升级为审核 DAG。
    if department in _FINANCE_DEPARTMENTS:
        if any(marker in task.query for marker in _FINANCE_REVIEW_MARKERS):
            return FINANCE_REVIEW_SCENARIO
        return FINANCE_SCENARIO
    # 部门上下文优先于跨部门重叠关键词，例如 hr 下的“高温补贴”不落入财务。
    if department in _HR_DEPARTMENTS:
        return HR_SCENARIO
    if department in _PROCUREMENT_DEPARTMENTS:
        return PROCUREMENT_SCENARIO
    if any(marker in task.query for marker in _FINANCE_REVIEW_MARKERS):
        return FINANCE_REVIEW_SCENARIO
    if any(marker in task.query for marker in _FINANCE_QUERY_MARKERS):
        return FINANCE_SCENARIO
    if any(marker in task.query for marker in _HR_QUERY_MARKERS):
        return HR_SCENARIO
    if any(marker in task.query for marker in _PROCUREMENT_QUERY_MARKERS):
        return PROCUREMENT_SCENARIO
    return None
