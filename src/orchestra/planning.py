"""拆解计划生成与校验：规划 DAG 子任务，并对计划质量做静态验证。"""
from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from typing import Any

from .contracts.subtask import SubtaskSpec
from .contracts.task import TaskInput
from .llm import LLMService
from .scenarios import ScenarioConfig

# 计划最大嵌套深度：DAG 节点内的递归 DAG 只允许再嵌套一层。
MAX_PLAN_DEPTH = 2
# DAG 节点当前支持的执行策略。
VALID_STRATEGIES = ("direct", "react", "dag")
# 框架内置工具名，PlanValidator 用于拦截非法工具名。
KNOWN_TOOLS = ("rag_search", "contract_context", "workspace_read", "workspace_list")
# 已知 Agent 角色。
KNOWN_ROLES = (
    "generalist",
    "contract_analyst",
    "risk_analyst",
    "reviewer",
    "finance_analyst",
    "procurement_agent",
    "hr_agent",
    "legal_analyst",
)

# 多分句、多步骤、工具依赖与 React 标记。
MULTI_CLAUSE_MARKERS = ("并且", "同时", "以及", "还有", "分别", "首先", "然后", "最后")
MULTI_STEP_MARKERS = (
    "流程", "步骤", "比较", "对比", "分析", "审查", "判断",
    "哪些", "怎么办", "生成", "检查", "清单",
)
TOOL_MARKERS = ("合同", "文档", "制度", "报销单", "表格", "材料")
REACT_MARKERS = ("调用", "工具", "检索", "审查", "核实")
SPLIT_PATTERN = re.compile(r"(?:并且|同时|以及|还有|然后|再|接下来)")
SEQUENTIAL_PATTERN = re.compile(r"(?:然后|再|接下来)")

_SCENARIO_ROLE = {
    "人事": "hr_agent",
    "风控": "risk_analyst",
    "财务": "finance_analyst",
    "招采": "procurement_agent",
}


@dataclass(frozen=True)
class DecompositionPlan:
    """一次拆解的产物：子任务、规划来源与依据。"""

    subtasks: tuple[SubtaskSpec, ...]
    planner: str = "rule"  # scenario | rule | llm
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 友好的计划结构。"""
        return {
            "planner": self.planner,
            "rationale": self.rationale,
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


@dataclass(frozen=True)
class PlanValidationResult:
    """计划校验结果：是否合法、错误清单、告警与最大深度。"""

    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    depth: int = 0

    def raise_if_invalid(self) -> None:
        """校验不通过时抛出 ValueError，便于 Router/评测器快速失败。"""
        if not self.valid:
            raise ValueError("拆解计划校验失败：" + "；".join(self.errors))


def split_parts(query: str) -> list[str]:
    """按连接词切分请求，返回最多 4 个子句。"""
    parts = [
        part.strip(" ，,。;；")
        for part in SPLIT_PATTERN.split(query)
        if part.strip(" ，,。;；")
    ]
    return parts[:4] if len(parts) > 1 else [query]


def build_subtasks(query: str, sequential: bool | None = None) -> tuple[SubtaskSpec, ...]:
    """按连接词生成通用 DAG 子任务。

    Args:
        query: 用户原始请求。
        sequential: 是否强制串行；为空时按连接词自动判断。

    Returns:
        子任务规格元组。
    """
    parts = split_parts(query)
    if sequential is None:
        sequential = bool(SEQUENTIAL_PATTERN.search(query))
    specs: list[SubtaskSpec] = []
    for index, part in enumerate(parts, start=1):
        dependencies = (f"t{index - 1}",) if sequential and index > 1 else ()
        specs.append(
            SubtaskSpec(
                id=f"t{index}",
                goal=part,
                dependencies=dependencies,
                metadata={"source": "rule_router"},
            )
        )
    return tuple(specs)


class DecompositionPlanner:
    """双通道拆解规划器：场景模板优先，未命中模板时走规则规划。"""

    def __init__(self, tool_names: tuple[str, ...] = KNOWN_TOOLS) -> None:
        """初始化规划器。

        Args:
            tool_names: 可选工具清单，用于规则规划时过滤不在框架内的工具。
        """
        self._tool_names = tuple(tool_names)

    def plan(
        self,
        task: TaskInput,
        scenario: ScenarioConfig | None = None,
        score: float = 0.0,
        threshold: float = 0.3,
    ) -> DecompositionPlan:
        """生成拆解计划。

        场景配置自带 subtasks 时直接使用模板；否则按通用规则拆解。
        """
        if scenario is not None and scenario.subtasks:
            return DecompositionPlan(
                subtasks=scenario.subtasks,
                planner="scenario",
                rationale=f"命中场景模板: {scenario.scenario_id}",
            )
        return self._rule_plan(task, scenario, score, threshold)

    async def plan_with_llm(
        self,
        task: TaskInput,
        llm: LLMService,
        scenario: ScenarioConfig | None = None,
        score: float = 0.0,
        threshold: float = 0.3,
    ) -> DecompositionPlan:
        """LLM 规划通道：输出结构化 JSON，校验失败自动回退规则规划。

        Args:
            task: 输入任务。
            llm: LLM 服务，模型需按提示词返回 JSON。
            scenario: 命中的业务场景，用于场景提示与兜底模板。
            score: 复杂度评分，写入兜底计划依据。
            threshold: 场景/通用阈值，写入兜底计划依据。

        Returns:
            DecompositionPlan：planner 为 llm 或回退后的 rule。
        """
        scenario_hint = scenario.scenario_id if scenario else "通用"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是企业多智能体框架的任务拆解规划器。只输出 JSON，"
                    "格式为 {\"subtasks\": [{\"id\": \"t1\", \"goal\": \"...\", "
                    "\"dependencies\": [], \"tools\": [], \"strategy\": \"direct\", "
                    "\"agent_role\": \"generalist\"}], \"rationale\": \"...\"}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"场景：{scenario_hint}；请求：{task.query}。"
                    "请拆成 1-4 个子任务，依赖关系只引用已声明的 id，"
                    "工具只能使用 rag_search/contract_context/workspace_read/workspace_list。"
                ),
            },
        ]
        result = await llm.complete(messages, max_tokens=1200, model=llm.default_model)
        plan = self._parse_llm_plan(result.text)
        if PlanValidator().validate(plan).valid:
            return plan
        fallback = self.plan(task, scenario, score, threshold)
        return DecompositionPlan(
            subtasks=fallback.subtasks,
            planner="rule",
            rationale=fallback.rationale + "；LLM 输出未通过校验，已回退规则规划",
        )

    @staticmethod
    def _parse_llm_plan(text: str) -> DecompositionPlan:
        """从 LLM 输出解析 DecompositionPlan；解析失败返回空计划供校验回退。"""
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return DecompositionPlan(subtasks=(), planner="llm", rationale="llm_output_invalid")
        try:
            payload = json.loads(text[start : end + 1])
        except ValueError:
            return DecompositionPlan(subtasks=(), planner="llm", rationale="llm_output_invalid")
        subtasks: list[SubtaskSpec] = []
        for item in payload.get("subtasks") or []:
            subtasks.append(
                SubtaskSpec(
                    id=str(item.get("id") or ""),
                    goal=str(item.get("goal") or ""),
                    dependencies=tuple(str(dep) for dep in item.get("dependencies") or ()),
                    tools=tuple(str(tool) for tool in item.get("tools") or ()),
                    strategy=str(item.get("strategy") or "direct"),
                    agent_role=str(item.get("agent_role") or "generalist"),
                )
            )
        return DecompositionPlan(
            subtasks=tuple(subtasks),
            planner="llm",
            rationale=str(payload.get("rationale") or "llm_plan"),
        )

    def _rule_plan(
        self,
        task: TaskInput,
        scenario: ScenarioConfig | None,
        score: float,
        threshold: float,
    ) -> DecompositionPlan:
        """规则规划器：按连接词切分，并补充工具与节点策略。"""
        parts = split_parts(task.query)
        sequential = bool(SEQUENTIAL_PATTERN.search(task.query))
        role = _SCENARIO_ROLE.get(scenario.department, "generalist") if scenario else "generalist"
        spec_tools: tuple[str, ...] = ()
        if scenario is not None and scenario.tools:
            spec_tools = tuple(tool for tool in scenario.tools if tool in self._tool_names)
        specs: list[SubtaskSpec] = []
        for index, part in enumerate(parts, start=1):
            dependencies = (f"t{index - 1}",) if sequential and index > 1 else ()
            part_tools = spec_tools if index == 1 else ()
            node_strategy = "react" if part_tools and any(
                marker in part for marker in REACT_MARKERS
            ) else "direct"
            specs.append(
                SubtaskSpec(
                    id=f"t{index}",
                    goal=part,
                    dependencies=dependencies,
                    tools=part_tools,
                    strategy=node_strategy,
                    agent_role=role,
                    metadata={"source": "rule_planner"},
                )
            )
        rationale = (
            f"规则规划: {len(parts)} 个子任务, score={score:.2f}, "
            f"threshold={threshold:.2f}"
        )
        return DecompositionPlan(tuple(specs), planner="rule", rationale=rationale)


class PlanValidator:
    """拆解计划静态校验器。"""

    def __init__(
        self,
        known_tools: tuple[str, ...] = KNOWN_TOOLS,
        known_roles: tuple[str, ...] = KNOWN_ROLES,
        max_depth: int = MAX_PLAN_DEPTH,
    ) -> None:
        """初始化校验器。

        Args:
            known_tools: 允许出现的工具名列表。
            known_roles: 允许出现的 Agent 角色列表。
            max_depth: 允许的最大嵌套计划深度。
        """
        self._known_tools = set(known_tools)
        self._known_roles = set(known_roles)
        self._max_depth = max_depth

    def validate(self, plan: DecompositionPlan) -> PlanValidationResult:
        """校验一个拆解计划。"""
        if not plan.subtasks:
            return PlanValidationResult(valid=False, errors=("计划不能为空",), depth=0)
        errors: list[str] = []
        warnings: list[str] = []
        seen: set[str] = set()
        graph: dict[str, set[str]] = {}
        for spec in plan.subtasks:
            graph[spec.id] = set(spec.dependencies)
            if not spec.id:
                errors.append("子任务 id 不能为空")
            elif spec.id in seen:
                errors.append(f"子任务 id 重复: {spec.id}")
            seen.add(spec.id)
            if not spec.goal or not spec.goal.strip():
                errors.append(f"子任务 {spec.id} 缺少目标")
            for tool in spec.tools:
                if tool not in self._known_tools:
                    errors.append(f"非法工具: {spec.id} -> {tool}")
            if spec.strategy not in VALID_STRATEGIES:
                errors.append(f"非法节点策略: {spec.id} -> {spec.strategy}")
            if spec.agent_role and spec.agent_role not in self._known_roles:
                errors.append(f"非法角色: {spec.id} -> {spec.agent_role}")
        for spec in plan.subtasks:
            for dep in spec.dependencies:
                if dep not in graph:
                    errors.append(f"依赖缺失: {spec.id} -> {dep}")
        errors.extend(self._cycle_errors(graph, seen))
        depth = max((self._depth(spec) for spec in plan.subtasks), default=0)
        if depth > self._max_depth:
            errors.append(f"计划深度 {depth} 超过上限 {self._max_depth}")
        return PlanValidationResult(
            valid=not errors,
            errors=tuple(dict.fromkeys(errors)),
            warnings=tuple(warnings),
            depth=depth,
        )

    def ensure_valid(self, plan: DecompositionPlan) -> DecompositionPlan:
        """校验通过后原样返回计划，失败时抛出 ValueError。"""
        self.validate(plan).raise_if_invalid()
        return plan

    def _cycle_errors(self, graph: dict[str, set[str]], ids: set[str]) -> list[str]:
        """用拓扑排序检测环依赖，返回错误信息列表。"""
        children: dict[str, list[str]] = {node_id: [] for node_id in ids}
        indegree = {node_id: 0 for node_id in ids}
        for node_id, deps in graph.items():
            for dep in deps:
                if dep not in children:
                    continue
                children[dep].append(node_id)
                indegree[node_id] += 1
        queue = deque([node_id for node_id, degree in indegree.items() if degree == 0])
        visited = 0
        while queue:
            node_id = queue.popleft()
            visited += 1
            for child in children[node_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if visited != len(ids):
            return ["存在循环依赖或不可达依赖"]
        return []

    def _depth(self, spec: SubtaskSpec, seen: tuple[int, ...] = ()) -> int:
        """递归计算计划深度，嵌套子任务视为下一层。"""
        children = spec.metadata.get("subtasks") or ()
        if not children:
            return 1
        if id(spec) in seen:
            return self._max_depth + 1
        nested_seen = seen + (id(spec),)
        child_depths = [self._depth(child, nested_seen) for child in children]
        return 1 + max(child_depths, default=0)
