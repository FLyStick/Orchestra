"""DAG 策略：按依赖拆解子任务，并行执行并汇总。

执行流程：
1. 把子任务按依赖关系分批：每批只执行"依赖已全部完成"的就绪节点；
2. 同批节点用 asyncio.gather 并发执行（受信号量限制最大并行数）；
3. 所有子任务完成后，再调用一次 LLM 把各子任务结果汇总成最终答案。
"""
from __future__ import annotations

import asyncio

from ..contracts.strategies import BaseStrategy, StrategyContext, StrategyResult, StrategyType
from ..contracts.subtask import SubtaskSpec
from ..llm import LLMResult, LLMService


class DAGStrategy(BaseStrategy):
    """DAG（有向无环图）执行策略：按依赖关系并行执行子任务并汇总。"""

    def __init__(self, llm: LLMService, max_parallel: int = 4) -> None:
        """初始化 DAG 策略。
        Args:
            llm: LLM 服务，用于执行子任务和最终汇总。
            max_parallel: 最大并行子任务数，防止并发过高打爆限流。
        """
        self._llm = llm
        self._max_parallel = max_parallel

    @property
    def name(self) -> StrategyType:
        """返回策略类型标识。"""
        return StrategyType.DAG

    async def execute(self, context: StrategyContext) -> StrategyResult:
        """执行 DAG 策略：分批并行执行子任务，最后汇总。
        Args:
            context: 策略执行上下文，包含子任务列表、查询文本、预算和工作区。

        Returns:
            策略结果：最终答案文本与 token 用量统计。

        Raises:
            RuntimeError: 子任务之间存在循环依赖或缺失依赖时抛出。
        """
        # 没有子任务时兜底：把整个查询当作唯一子任务 t1。
        subtasks = list(context.subtasks) or [SubtaskSpec(id="t1", goal=context.query)]
        # 建立 id -> 子任务规格 的映射，便于按依赖查找。
        by_id = {spec.id: spec for spec in subtasks}
        # remaining 记录尚未完成的子任务 id 集合。
        remaining = set(by_id)
        # results 记录已完成子任务的 id -> 输出文本。
        results: dict[str, str] = {}
        # rows 收集所有 LLM 调用结果，用于统计 token 用量。
        rows: list[LLMResult] = []
        # 信号量：限制同时执行的子任务数量。
        semaphore = asyncio.Semaphore(self._max_parallel)

        def lazy_limit() -> int | None:
            """返回单次 LLM 调用的 token 上限；未配置预算时返回 None（不限制）。"""
            if context.budget and context.budget.per_agent_tokens > 0:
                return context.budget.per_agent_tokens
            return None

        async def run_subtask(spec: SubtaskSpec) -> tuple[str, str]:
            """执行单个子任务：调用 LLM 并把结果写入工作区。

            Returns:
                (子任务 id, 输出文本) 元组。
            """
            # 用信号量控制并发，避免同时发起过多请求。
            async with semaphore:
                # 构造子任务执行消息：系统提示词 + 子任务目标。
                messages = [
                    {
                        "role": "system",
                        "content": "你是企业内部多智能体编排框架中的子任务执行者。",
                    },
                    {"role": "user", "content": spec.goal},
                ]
                # 调用 LLM 完成子任务，并应用预算限制。
                result = await self._llm.complete(messages, max_tokens=lazy_limit())
                # 把子任务结果落盘到工作区 subtasks/{id}.md，便于追溯。
                await context.workspace.write(f"subtasks/{spec.id}.md", result.text)
                # 记录本次调用，用于最终统计 token 用量。
                rows.append(result)
                return spec.id, result.text

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

        # 子任务全部完成后，由一次汇总调用合并为最终答案。
        # 按 id 顺序拼接各子任务结果，保证汇总输入稳定有序。
        joined = "\n".join(f"## {spec_id}\n{results[spec_id]}" for spec_id in by_id)
        messages = [
            {"role": "system", "content": "你是任务汇总助手，负责把子任务结果整理成最终答案。"},
            {"role": "user", "content": f"汇总以下子任务结果：\n{joined}"},
        ]
        # 调用 LLM 生成最终汇总答案。
        final = await self._llm.complete(messages, max_tokens=lazy_limit())
        # 最终答案写入工作区根目录 answer.md。
        await context.workspace.write("answer.md", final.text)
        rows.append(final)
        # 汇总所有调用的 token 用量并返回结果。
        return StrategyResult(
            output=final.text,
            token_usage={
                "input_tokens": sum(row.input_tokens for row in rows),
                "output_tokens": sum(row.output_tokens for row in rows),
                "calls": len(rows),
                "model": rows[-1].model if rows else "unknown",
            },
        )