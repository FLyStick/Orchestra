"""DAG 策略：按依赖拆解子任务，并行执行并汇总。

执行流程：
1. 把子任务按依赖关系分批：每批只执行“依赖已全部完成”的就绪节点；
2. 同批节点用 asyncio.gather 并发执行（受信号量限制最大并行数）；
3. 节点按 strategy 分派：direct 先执行声明工具再单次 LLM；
   react 复用 React 工具循环；dag 递归执行嵌套 DAG（深度限制 2 层）；
4. 所有子任务完成后，再调用一次 LLM 把各子任务结果汇总成最终答案。
"""
from __future__ import annotations

import asyncio

from ..budget import TokenBudgetTracker
from ..contracts.events import EventType
from ..contracts.strategies import (
    BaseStrategy,
    StrategyContext,
    StrategyResult,
    StrategyType,
    ToolCall,
)
from ..contracts.subtask import SubtaskSpec
from ..llm import LLMResult, LLMService, estimate_tokens
from ..tools import ToolRegistry, create_tool_registry
from .react import ReactStrategy

# 递归 DAG 最大深度：限制为 2 层，避免循环与上下文爆炸。
MAX_NESTED_DEPTH = 2
# 递归 DAG 节点通过 metadata["subtasks"] 声明子任务。
NESTED_SUBTASKS_KEY = "subtasks"


class DAGStrategy(BaseStrategy):
    """DAG（有向无环图）执行策略：按依赖关系并行执行子任务并汇总。"""

    def __init__(
        self,
        llm: LLMService,
        max_parallel: int = 4,
        registry: ToolRegistry | None = None,
    ) -> None:
        """初始化 DAG 策略。

        Args:
            llm: LLM 服务，用于执行子任务和最终汇总。
            max_parallel: 最大并行子任务数，防止并发过高打爆限流。
            registry: 工具注册表；不传则使用默认注册表（含 RAG/合同/工作区工具）。
        """
        self._llm = llm
        self._max_parallel = max_parallel
        self._registry = registry or create_tool_registry()
        # React 作为节点级执行模式复用，与 DAG 共用同一工具注册表。
        self._react = ReactStrategy(llm, self._registry)

    @property
    def name(self) -> StrategyType:
        return StrategyType.DAG

    def _emit(self, context: StrategyContext, event_type: str, payload: dict[str, object]) -> None:
        """向外部推送事件（若上下文配置了事件回调）。"""
        if context.emit is not None:
            context.emit(event_type, payload)

    async def execute(self, context: StrategyContext) -> StrategyResult:
        """执行 DAG 策略：分批并行执行子任务，最后汇总。

        Args:
            context: 策略执行上下文，包含子任务列表、查询文本、预算和工作区。
        Returns:
            策略结果：最终答案文本、工具调用与 token 用量统计。
        Raises:
            RuntimeError: 子任务之间存在循环依赖或缺失依赖时抛出。
            BudgetExceededError: 总预算不足以发起下一次 LLM 调用时抛出。
        """
        # 没有子任务时兜底：把整个查询当作唯一子任务 t1。
        subtasks = list(context.subtasks) or [SubtaskSpec(id="t1", goal=context.query)]
        # 预算跟踪器：Simple/DAG/React 共用同一套总额限制与降级逻辑。
        tracker = TokenBudgetTracker(context.budget)
        # 同一 tracker 贯穿 direct/react/dag 节点，保证 Token 预算全局累计。
        results, rows, tool_calls = await self._execute_nodes(
            context,
            subtasks,
            tracker,
            depth=1,
        )

        # 子任务全部完成后，由一次汇总调用合并为最终答案。
        # 按 id 顺序拼接各子任务结果，保证汇总输入稳定有序。
        joined = "\n".join(
            f"## {spec.id}\n{results[spec.id]}"
            for spec in subtasks
        )
        messages = [
            {"role": "system", "content": "你是任务汇总助手，负责把子任务结果整理成最终答案。"},
            {"role": "user", "content": f"汇总以下子任务结果：\n{joined}"},
        ]
        input_estimate = estimate_tokens(
            "".join(m.get("content", "") for m in messages)
        )
        tracker.ensure_available(input_estimate)
        model = tracker.choose_model(self._llm.default_model, self._llm.fallback_model)
        # 调用 LLM 生成最终汇总答案。
        final = await self._llm.complete(
            messages,
            max_tokens=tracker.next_max_tokens(input_estimate),
            model=model,
        )
        tracker.record(final.input_tokens, final.output_tokens)
        # 最终答案写入工作区根目录 answer.md。
        await context.workspace.write("answer.md", final.text)
        self._emit(
            context,
            EventType.WORKSPACE_UPDATED.value,
            {"path": "answer.md"},
        )
        rows.append(final)
        # 汇总所有调用的 token 用量并返回结果。
        return StrategyResult(
            output=final.text,
            tool_calls=tool_calls,
            token_usage={
                "input_tokens": sum(row.input_tokens for row in rows),
                "output_tokens": sum(row.output_tokens for row in rows),
                "calls": len(rows),
                "model": rows[-1].model if rows else "unknown",
            },
        )

    async def _execute_nodes(
        self,
        context: StrategyContext,
        subtasks: list[SubtaskSpec],
        tracker: TokenBudgetTracker,
        depth: int,
    ) -> tuple[dict[str, str], list[LLMResult], list[ToolCall]]:
        """执行一批子任务，返回结果文本、LLM 调用与工具调用。

        Args:
            context: 策略执行上下文。
            subtasks: 本层子任务列表（递归 DAG 时传入嵌套子任务）。
            tracker: 共享 Token 预算跟踪器。
            depth: 当前 DAG 深度，从 1 开始。

        Returns:
            (子任务结果映射, LLM 调用列表, 工具调用列表) 三元组。
        """
        by_id = {spec.id: spec for spec in subtasks}
        # remaining 记录尚未完成的子任务 id 集合。
        remaining = set(by_id)
        # results 记录已完成子任务的 id -> 输出文本。
        results: dict[str, str] = {}
        # rows 收集本层所有 LLM 调用结果，用于统计 token 用量。
        rows: list[LLMResult] = []
        tool_calls: list[ToolCall] = []
        # 信号量：限制同时执行的子任务数量。
        semaphore = asyncio.Semaphore(self._max_parallel)

        async def run_subtask(spec: SubtaskSpec) -> tuple[str, str]:
            """执行单个子任务：direct 声明工具、react 工具循环、dag 递归。

            Returns:
                (子任务 id, 输出文本) 元组。
            """
            async with semaphore:
                # 子任务开始事件：便于 SSE 展示当前执行到哪个 Agent。
                self._emit(
                    context,
                    EventType.AGENT_STARTED.value,
                    {
                        "subtask_id": spec.id,
                        "goal": spec.goal,
                        "agent_role": spec.agent_role,
                    },
                )
                # 依赖子任务的输出一并注入，保证串行阶段能复用前序结论。
                dependency_parts = [
                    f"### {dep_id} 结果\n{results[dep_id]}"
                    for dep_id in spec.dependencies
                    if dep_id in results
                ]
                user_content = spec.goal
                if dependency_parts:
                    user_content = (
                        "前置子任务结果：\n"
                        + "\n".join(dependency_parts)
                        + "\n\n任务目标：" + spec.goal
                    )

                if spec.strategy == "react":
                    # react 节点：模型自主决定工具调用顺序，不预跑声明工具。
                    node_result = await self._react.run_node(
                        context,
                        tracker,
                        query=user_content,
                        subtask_id=spec.id,
                        agent_role=spec.agent_role,
                        tool_names=spec.tools,
                    )
                    output = node_result.output
                    rows.extend(node_result.rows)
                    tool_calls.extend(node_result.tool_calls)
                    # 节点级轨迹写入 DAG 命名空间，便于按子任务追溯。
                    trace_path = f"dag/{spec.id}/react_trace.md"
                    await context.workspace.write(
                        trace_path,
                        "\n".join(node_result.trace) or output,
                    )
                    self._emit(
                        context,
                        EventType.WORKSPACE_UPDATED.value,
                        {"path": trace_path, "subtask_id": spec.id},
                    )
                elif spec.strategy == "dag":
                    # dag 节点：递归执行嵌套 DAG，受 MAX_NESTED_DEPTH 限制。
                    if depth >= MAX_NESTED_DEPTH:
                        raise RuntimeError(
                            f"递归 DAG 超过最大深度限制：{MAX_NESTED_DEPTH}"
                        )
                    nested = tuple(spec.metadata.get(NESTED_SUBTASKS_KEY) or ())
                    if not nested:
                        raise RuntimeError(
                            f"递归 DAG 节点 {spec.id} 缺少 metadata['subtasks']"
                        )
                    nested_results, nested_rows, nested_calls = await self._execute_nodes(
                        context,
                        list(nested),
                        tracker,
                        depth + 1,
                    )
                    rows.extend(nested_rows)
                    tool_calls.extend(nested_calls)
                    # 嵌套子任务结果合并为该节点的输出，交给上层依赖节点。
                    output = "\n".join(
                        f"## {child.id}\n{nested_results[child.id]}"
                        for child in nested
                    )
                    self._emit(
                        context,
                        EventType.AGENT_COMPLETED.value,
                        {
                            "subtask_id": spec.id,
                            "agent_role": spec.agent_role,
                            "model": rows[-1].model if rows else "unknown",
                        },
                    )
                elif spec.strategy != "direct":
                    raise ValueError(f"unsupported subtask strategy: {spec.strategy}")
                else:
                    # direct 节点：依次执行声明工具，结果作为观察注入 LLM 上下文。
                    tool_messages: list[dict[str, str]] = []
                    for tool_name in spec.tools:
                        # 工具参数优先取场景/子任务预置值，未配置时使用空参数。
                        tool_arguments = dict(
                            (spec.metadata.get("tool_arguments") or {}).get(tool_name, {})
                        )
                        tool_calls.append(ToolCall(name=tool_name, arguments=tool_arguments))
                        self._emit(
                            context,
                            EventType.TOOL_CALLED.value,
                            {
                                "subtask_id": spec.id,
                                "tool": tool_name,
                                "arguments": tool_arguments,
                            },
                        )
                        tool = self._registry.get(tool_name)
                        if tool is None:
                            available = ", ".join(
                                schema["name"] for schema in self._registry.list_schemas()
                            )
                            output = f"工具不存在：{tool_name}，可用工具：{available}"
                            success = False
                        else:
                            try:
                                tool_result = await tool.run(tool_arguments, context)
                                output = tool_result.output
                                success = tool_result.success
                            except Exception as exc:
                                output = f"工具执行异常：{exc}"
                                success = False
                        # 工具结果写入工作区，便于后续子任务与人工追溯。
                        tool_path = f"dag/{spec.id}/{tool_name}.md"
                        await context.workspace.write(tool_path, output)
                        self._emit(
                            context,
                            EventType.TOOL_COMPLETED.value,
                            {
                                "subtask_id": spec.id,
                                "tool": tool_name,
                                "path": tool_path,
                                "success": success,
                            },
                        )
                        self._emit(
                            context,
                            EventType.WORKSPACE_UPDATED.value,
                            {"path": tool_path, "subtask_id": spec.id},
                        )
                        tool_messages.append(
                            {"role": "user", "content": f"工具输出({tool_name}): {output}"}
                        )

                    # 构造子任务执行消息：系统提示词 + 工具观察 + 子任务目标。
                    messages = [
                        {
                            "role": "system",
                            "content": "你是企业内部多智能体编排框架中的子任务执行者。",
                        },
                        *tool_messages,
                        {"role": "user", "content": user_content},
                    ]
                    input_estimate = estimate_tokens(
                        "".join(m.get("content", "") for m in messages)
                    )
                    tracker.ensure_available(input_estimate)
                    model = tracker.choose_model(
                        self._llm.default_model, self._llm.fallback_model
                    )
                    # 调用 LLM 完成子任务，并应用总预算与单 Agent 预算限制。
                    result = await self._llm.complete(
                        messages,
                        max_tokens=tracker.next_max_tokens(input_estimate),
                        model=model,
                    )
                    tracker.record(result.input_tokens, result.output_tokens)
                    self._emit(
                        context,
                        EventType.TOKEN_UPDATED.value,
                        {"subtask_id": spec.id, "token_usage": tracker.usage},
                    )
                    self._emit(
                        context,
                        EventType.AGENT_COMPLETED.value,
                        {
                            "subtask_id": spec.id,
                            "agent_role": spec.agent_role,
                            "model": result.model,
                        },
                    )
                    output = result.text
                    rows.append(result)

                # 节点结果统一写入 subtasks/{id}.md，供依赖节点与人工复用。
                await context.workspace.write(f"subtasks/{spec.id}.md", output)
                self._emit(
                    context,
                    EventType.WORKSPACE_UPDATED.value,
                    {"path": f"subtasks/{spec.id}.md", "subtask_id": spec.id},
                )
                return spec.id, output

        # 按依赖分批执行：就绪节点并行，未就绪节点等待依赖完成。
        while remaining:
            # 就绪节点 = 所有依赖都已在 results 中的子任务。
            ready = [
                by_id[spec_id]
                for spec_id in remaining
                if all(dep in results for dep in by_id[spec_id].dependencies)
            ]
            # 没有就绪节点但还有未完成任务 => 存在循环依赖或依赖缺失。
            if not ready:
                raise RuntimeError("DAG 存在循环依赖或缺失依赖")
            # 并发执行本批所有就绪节点，等待全部完成后进入下一批。
            outcomes = await asyncio.gather(*(run_subtask(spec) for spec in ready))
            # 把本批结果写入 results，并从 remaining 中移除。
            for spec_id, text in outcomes:
                results[spec_id] = text
                remaining.discard(spec_id)
        return results, rows, tool_calls

