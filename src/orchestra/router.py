"""规则路由 v2：特征评分、置信度、场景路由与拆解计划。

本模块是编排框架的"路由层"：提取结构化特征，计算可解释的复杂度分数与
置信度；命中预置业务场景时按场景阈值选择 Simple/React/DAG，未命中时走
通用规则并调用 DecompositionPlanner 生成可验证的拆解计划。
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import get_settings
from .contracts.routing import RoutingDecision, RoutingFeatures
from .contracts.strategies import StrategyType
from .contracts.task import TaskInput
from .planning import (
    DecompositionPlanner,
    PlanValidator,
    REACT_MARKERS,
    split_parts,
)
from .scenarios import ScenarioConfig, select_scenario

# 通用复杂度阈值：分数 >= 该值的无场景请求走 DAG。
SIMPLE_THRESHOLD = 0.3
# 各业务场景独立阈值，优先于通用阈值。
DEFAULT_SCENARIO_THRESHOLDS: dict[str, float] = {
    "hr_policy_qa": 0.30,
    "risk_contract_review": 0.25,
    "finance_policy_qa": 0.35,
    "finance_invoice_review": 0.30,
    "procurement_process_qa": 0.30,
}
# 上下文中的工作区产物标记，命中后说明已有可复用结果。
WORKSPACE_CONTEXT_KEYS = (
    "workspace_files",
    "workspace_context",
    "existing_artifacts",
    "file_paths",
    "files",
)
# 工作区中可复用的任务上下文标记：命中后说明任务可以引用既有产物。
WORKSPACE_CONTEXT_FLAGS = ("workspace", "session_context", "task_context")


@dataclass(frozen=True)
class ScoredRouting:
    """ScorerV2 的完整输出：分数、置信度、特征与可解释因子。"""

    score: float
    confidence: float
    features: RoutingFeatures
    reasons: tuple[str, ...]


def _extract_features(query: str, context: dict | None) -> RoutingFeatures:
    """从原始请求与上下文提取结构化路由特征。"""
    context = context or {}
    department = str(context.get("department") or "").strip().lower()
    parts = split_parts(query)
    clause_hits = sum(1 for marker in ("并且", "同时", "以及", "还有", "分别", "首先", "然后", "最后") if marker in query)
    step_hits = sum(1 for marker in ("流程", "步骤", "比较", "对比", "分析", "审查", "判断", "哪些", "怎么办", "生成", "检查", "清单") if marker in query)
    tool_hits = sum(1 for marker in ("合同", "文档", "制度", "报销单", "表格", "材料") if marker in query)
    react_hits = sum(1 for marker in REACT_MARKERS if marker in query)
    has_workspace_context = any(
        context.get(key) for key in WORKSPACE_CONTEXT_KEYS
    ) or any(context.get(key) for key in WORKSPACE_CONTEXT_FLAGS)
    return RoutingFeatures(
        text_length=len(query.strip()),
        clause_count=max(1, len(parts)),
        clause_hits=clause_hits,
        step_hits=step_hits,
        tool_hits=tool_hits,
        react_hits=react_hits,
        has_department=bool(department),
        has_workspace_context=has_workspace_context,
    )


class ScorerV2:
    """复杂度评分 v2：输出分数、置信度、结构化特征与可解释因子。"""

    def __init__(self, ambiguous_band: tuple[float, float] = (0.25, 0.35)) -> None:
        """初始化评分器。

        Args:
            ambiguous_band: 低置信复核区间，落在此区间的分数置信度更低。
        """
        self.ambiguous_band = ambiguous_band

    def evaluate(self, query: str, context: dict | None = None) -> ScoredRouting:
        """计算请求的路由评分。"""
        features = _extract_features(query, context)
        score = self._score(features)
        confidence = self._confidence(features, score)
        reasons = self._reasons(features, score)
        return ScoredRouting(
            score=score,
            confidence=confidence,
            features=features,
            reasons=tuple(reasons),
        )

    def score(self, query: str, context: dict | None = None) -> float:
        """兼容旧评分器接口，只返回复杂度分数。"""
        return self.evaluate(query, context).score

    def _score(self, features: RoutingFeatures) -> float:
        """加权打分并封顶 0.95，分数越高越倾向复杂策略。"""
        score = 0.0
        # 长文本本身更复杂；60 字符内不加分，超 120 字符再加一档。
        if features.text_length > 120:
            score += 0.10
        elif features.text_length > 60:
            score += 0.05
        score += min(0.36, features.clause_hits * 0.12)
        score += min(0.36, features.step_hits * 0.12)
        score += min(0.18, features.tool_hits * 0.06)
        score += min(0.16, features.react_hits * 0.08)
        return round(min(0.95, score), 2)

    def _confidence(self, features: RoutingFeatures, score: float) -> float:
        """置信度：远离低置信区间时更高，混合信号会降低置信度。"""
        low, high = self.ambiguous_band
        midpoint = (low + high) / 2
        distance = abs(score - midpoint)
        confidence = min(1.0, max(0.42, 0.46 + distance / 0.5))
        if features.react_hits and features.step_hits:
            confidence -= 0.12
        if features.clause_hits and features.tool_hits:
            confidence -= 0.08
        if not features.has_department and features.clause_count > 2:
            confidence -= 0.10
        if features.has_workspace_context:
            confidence += 0.05
        return round(min(1.0, max(0.35, confidence)), 2)

    def _reasons(self, features: RoutingFeatures, score: float) -> list[str]:
        """把特征命中与分数落点转换成可解释因子。"""
        reasons: list[str] = []
        if features.text_length > 120:
            reasons.append(f"long_text={features.text_length}")
        if features.clause_hits:
            reasons.append(f"clause_markers={features.clause_hits}")
        if features.step_hits:
            reasons.append(f"step_markers={features.step_hits}")
        if features.tool_hits:
            reasons.append(f"tool_markers={features.tool_hits}")
        if features.react_hits:
            reasons.append(f"react_markers={features.react_hits}")
        if features.has_department:
            reasons.append("department_context")
        if features.has_workspace_context:
            reasons.append("workspace_context")
        low, high = self.ambiguous_band
        if low <= score <= high:
            reasons.append(f"ambiguous_band={low:.2f}-{high:.2f}")
        reasons.append(f"complexity_score={score:.2f}")
        return reasons


# 保留旧类名，兼容 MVP 阶段外部调用。
class ComplexityScorer(ScorerV2):
    """ScorerV2 的兼容别名。"""


class RuleRouter:
    """规则路由器 v2：场景阈值 + 特征置信度 + 可验证拆解计划。"""

    def __init__(
        self,
        threshold: float = SIMPLE_THRESHOLD,
        scorer: ScorerV2 | None = None,
        scenario_thresholds: dict[str, float] | None = None,
        ambiguous_band: tuple[float, float] | None = None,
        planner: DecompositionPlanner | None = None,
        validator: PlanValidator | None = None,
    ) -> None:
        """初始化路由器。

        Args:
            threshold: 通用复杂度阈值。
            scorer: 自定义评分器，需提供 evaluate(query, context)。
            scenario_thresholds: 场景级阈值，覆盖默认值。
            ambiguous_band: 低置信复核区间，未传时读取 .env 配置。
            planner: 拆解规划器，未传时使用规则规划器。
            validator: 计划校验器，未传时使用默认静态校验器。
        """
        settings = get_settings()
        band = ambiguous_band or settings.ambiguous_band
        self.threshold = threshold
        self.scorer = scorer or ScorerV2(ambiguous_band=band)
        merged = {
            **DEFAULT_SCENARIO_THRESHOLDS,
            "hr_policy_qa": settings.hr_scenario_threshold,
        }
        if scenario_thresholds:
            merged.update(scenario_thresholds)
        self.scenario_thresholds = merged
        self.planner = planner or DecompositionPlanner()
        self.validator = validator or PlanValidator()

    def _threshold_for(self, scenario: ScenarioConfig | None) -> float:
        """返回场景专属阈值；无场景时返回通用阈值。"""
        if scenario is None:
            return self.threshold
        return self.scenario_thresholds.get(scenario.scenario_id, self.threshold)

    def route(self, task: TaskInput) -> RoutingDecision:
        """对单个任务进行路由决策。

        Args:
            task: 输入任务，包含请求文本、上下文、预算和可选策略。

        Returns:
            RoutingDecision：策略、分数、置信度、特征、原因与子任务列表。
        """
        scored = self.scorer.evaluate(task.query, task.context)
        scenario = select_scenario(task)
        threshold = self._threshold_for(scenario)
        reasons = list(scored.reasons)
        subtasks: tuple = ()

        # 显式策略
        if task.strategy:
            try:
                strategy = StrategyType(task.strategy.lower())
            except ValueError as exc:
                raise ValueError(f"unsupported strategy: {task.strategy}") from exc
            reasons.append(f"explicit_strategy={strategy.value}")
            if strategy == StrategyType.DAG:
                plan = self.planner.plan(task, scenario, scored.score, threshold)
                self.validator.ensure_valid(plan)
                subtasks = plan.subtasks
        # 场景路由
        elif scenario is not None:
            react_requested = any(marker in task.query for marker in REACT_MARKERS)
            dag_markers = scenario.metadata.get("dag_markers") or ()
            simple_dag = (
                scenario.strategy == StrategyType.SIMPLE
                and any(marker in task.query for marker in dag_markers)
            )
            if (
                scenario.scenario_id == "hr_policy_qa"
                and scored.score < threshold
                and not react_requested
            ):
                # HR 高频单跳问题默认 Simple + RAG，降低延迟与成本。
                strategy = StrategyType.SIMPLE
            elif react_requested and scenario.strategy == StrategyType.SIMPLE:
                # 用户显式要求调用工具/检索时，Simple 场景升级为 React。
                strategy = StrategyType.REACT
            elif simple_dag:
                # 招采等流程类问题升级为 DAG，按场景模板或规则拆解。
                strategy = StrategyType.DAG
                plan = self.planner.plan(task, scenario, scored.score, threshold)
                self.validator.ensure_valid(plan)
                subtasks = plan.subtasks
            else:
                strategy = scenario.strategy
                if strategy == StrategyType.DAG:
                    plan = self.planner.plan(task, scenario, scored.score, threshold)
                    self.validator.ensure_valid(plan)
                    subtasks = plan.subtasks
            reasons.append(f"scenario_match={scenario.scenario_id}, strategy={strategy.value}")
        # 通用路由
        elif react_requested := any(marker in task.query for marker in REACT_MARKERS):
            strategy = StrategyType.REACT
            reasons.append("query_contains_react_markers")
        elif scored.score >= threshold:
            # 通用请求达到阈值时走 DAG，子任务由规划器生成并校验。
            strategy = StrategyType.DAG
            plan = self.planner.plan(task, scenario, scored.score, threshold)
            self.validator.ensure_valid(plan)
            subtasks = plan.subtasks
            reasons.append(f"complexity_above_threshold={threshold:.2f}")
        else:
            strategy = StrategyType.SIMPLE
            reasons.append(f"complexity_below_threshold={threshold:.2f}")

        # 低置信的 Simple 决策留升级钩子，Simple 策略执行时按事件升级到 React。
        if strategy == StrategyType.SIMPLE and scored.confidence < 0.5:
            reasons.append(f"low_confidence={scored.confidence:.2f}, escalation=react")

        reason_text = "; ".join(reasons)
        return RoutingDecision(
            strategy=strategy,
            complexity_score=scored.score,
            confidence=scored.confidence,
            reasons=tuple(reasons),
            reason=reason_text,
            features=scored.features,
            budget=task.budget,
            subtasks=subtasks,
            scenario_id=scenario.scenario_id if scenario else None,
        )
