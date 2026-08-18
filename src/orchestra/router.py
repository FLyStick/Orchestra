"""规则路由：复杂度评分、策略选择与子任务拆解。

本模块是编排框架的"路由层"：根据用户请求的文本特征（长度、连接词、
步骤性词汇、工具类词汇）计算一个复杂度分数，再依据分数决定采用
简单策略（SIMPLE）、DAG 策略还是 React 工具循环策略，并在 DAG 策略下
把请求拆成多个子任务。
"""
from __future__ import annotations

import re

from .contracts.routing import RoutingDecision, SubtaskSpec
from .contracts.strategies import StrategyType
from .contracts.task import TaskInput

# 复杂度阈值：分数 >= 该值时走 DAG 策略，否则走 SIMPLE 策略。
SIMPLE_THRESHOLD = 0.3
# 多分句标记：出现这些词说明请求包含多个并列/递进的分句。
MULTI_CLAUSE_MARKERS = ("并且", "同时", "以及", "还有", "分别", "首先", "然后", "最后")
# 多步骤标记：出现这些词说明请求隐含多步骤流程或需要分析判断。
MULTI_STEP_MARKERS = (
    "流程", "步骤", "比较", "对比", "分析", "审查", "判断",
    "哪些", "怎么办", "生成", "检查", "清单",
)
# 工具依赖标记：出现这些词说明请求可能依赖具体工具/文档资源。
TOOL_MARKERS = ("合同", "文档", "制度", "报销单", "表格", "材料")
# React 标记：出现这些词说明请求需要检索、审查或调用工具，优先走 React 循环。
REACT_MARKERS = ("调用", "工具", "检索", "审查", "核实")
# 分句切分正则：按这些连接词把请求拆成多个子句。
SPLIT_PATTERN = re.compile(r"(?:并且|同时|以及|还有|然后|再|接下来)")
# 串行依赖正则：出现这些词说明子任务之间存在先后顺序（串行依赖）。
SEQUENTIAL_PATTERN = re.compile(r"(?:然后|再|接下来)")


# MVP 先使用可解释的加权规则，后续可替换为学习路由。
class ComplexityScorer:
    """复杂度评分器：基于关键词命中情况给请求打一个 0~0.95 的复杂度分。"""

    def score(self, query: str, context: dict | None = None) -> float:
        """计算请求的复杂度分数。

        Args:
            query: 用户原始请求文本。
            context: 预留的上下文参数（当前未使用，便于后续扩展）。

        Returns:
            四舍五入到两位小数的复杂度分数，范围 [0, 0.95]。
        """
        score = 0.0
        # 长文本（>60 字符）本身更复杂，加 0.1 分。
        if len(query) > 60:
            score += 0.1
        # 多分句、多步骤、工具依赖分别加权并封顶，避免单条分数异常。
        # 每个分句标记 +0.15，最多加 0.4 分。
        clause_hits = sum(1 for marker in MULTI_CLAUSE_MARKERS if marker in query)
        score += min(0.4, clause_hits * 0.15)
        # 每个步骤标记 +0.1，最多加 0.3 分。
        step_hits = sum(1 for marker in MULTI_STEP_MARKERS if marker in query)
        score += min(0.3, step_hits * 0.1)
        # 每个工具标记 +0.05，最多加 0.15 分。
        tool_hits = sum(1 for marker in TOOL_MARKERS if marker in query)
        score += min(0.15, tool_hits * 0.05)
        # 总分封顶 0.95，避免极端情况分数溢出。
        return round(min(0.95, score), 2)


def split_parts(query: str) -> list[str]:
    """按连接词把请求切分成多个子句。

    Args:
        query: 用户原始请求文本。

    Returns:
        切分后的子句列表；若无法切分则返回 [query] 本身。
        最多保留前 4 个子句，防止拆解过碎。
    """
    # 先按连接词切分，再对每个子句去除首尾空白和常见标点，过滤空串。
    parts = [part.strip(" ，,。;；") for part in SPLIT_PATTERN.split(query) if part.strip(" ，,。;；")]
    # 能切出多个子句时最多取前 4 个；否则整体作为一个子句。
    return parts[:4] if len(parts) > 1 else [query]


# 按连接词切分子任务；含“然后/再”时为串行依赖，否则可并行。
def build_subtasks(query: str) -> list[SubtaskSpec]:
    """把请求拆解为子任务规格列表。

    规则：按连接词切分请求；若请求中出现"然后/再/接下来"等词，
    则子任务按顺序串行执行（每个子任务依赖前一个），否则全部可并行。

    Args:
        query: 用户原始请求文本。

    Returns:
        子任务规格列表，每个子任务带自增 id（t1、t2...）、目标文本和依赖关系。
    """
    parts = split_parts(query)
    # 是否串行：出现串行连接词则为 True。
    sequential = bool(SEQUENTIAL_PATTERN.search(query))
    specs: list[SubtaskSpec] = []
    for index, part in enumerate(parts, start=1):
        # 串行模式下，除第一个子任务外，每个子任务依赖前一个（t{index-1}）。
        dependencies = (f"t{index - 1}",) if sequential and index > 1 else ()
        specs.append(
            SubtaskSpec(
                id=f"t{index}",
                goal=part,
                dependencies=dependencies,
                metadata={"source": "rule_router"},  # 标记来源，便于溯源。
            )
        )
    return specs


class RuleRouter:
    """规则路由器：根据复杂度分数决定执行策略并生成子任务。"""

    def __init__(self, threshold: float = SIMPLE_THRESHOLD, scorer: ComplexityScorer | None = None) -> None:
        """初始化路由器。

        Args:
            threshold: 复杂度阈值，分数 >= 阈值时走 DAG 策略。
            scorer: 自定义评分器；不传则使用默认的 ComplexityScorer。
        """
        self.threshold = threshold
        self.scorer = scorer or ComplexityScorer()

    def route(self, task: TaskInput) -> RoutingDecision:
        """对单个任务进行路由决策。

        Args:
            task: 输入任务，包含请求文本、上下文、预算和可选策略。

        Returns:
            路由决策：包含选定的策略、复杂度分数、决策原因和子任务列表。

        Raises:
            ValueError: 调用方显式指定的策略名不受支持时抛出。
        """
        # 先计算请求的复杂度分数。
        score = self.scorer.score(task.query, task.context)
        # 未拆分任务时保持空元组，避免后续分支引用未绑定变量。
        subtasks: tuple[SubtaskSpec, ...] = ()
        # 调用方未显式指定策略时，按复杂度阈值与 React 标记自动路由。
        if task.strategy:
            # 显式策略：把字符串转成枚举，非法值抛错。
            try:
                strategy = StrategyType(task.strategy.lower())
            except ValueError as exc:
                raise ValueError(f"unsupported strategy: {task.strategy}") from exc
            reason = f"explicit strategy: {strategy.value}"
        elif any(marker in task.query for marker in REACT_MARKERS):
            # 需要检索、审查或调用工具的请求优先走 React 工具循环。
            strategy = StrategyType.REACT
            reason = "query contains tool/react markers"
        elif score >= self.threshold:
            # 自动路由：分数达到阈值走 DAG（多子任务），否则走 SIMPLE（单任务）。
            strategy = StrategyType.DAG
            subtasks = tuple(build_subtasks(task.query))
            reason = f"complexity_score={score:.2f}, threshold={self.threshold:.2f}"
        else:
            # 低复杂度请求直接回答，不拆子任务。
            strategy = StrategyType.SIMPLE
            reason = f"complexity_score={score:.2f}, routing to simple strategy"

        # 组装并返回路由决策对象。
        return RoutingDecision(
            strategy=strategy,
            complexity_score=score,
            reason=reason,
            budget=task.budget,
            subtasks=subtasks,
        )
