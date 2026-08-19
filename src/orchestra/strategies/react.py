"""React 策略：模型在“思考-工具调用-观察”循环中完成多步任务。

执行流程：
1. 模型输出 JSON 工具调用 → 解析并执行工具；
2. 把工具输出作为“观察”追加到对话历史；
3. 信息不足时继续调用工具，信息充分后生成最终答案；
4. 达到最大迭代次数仍未完成时，基于已有观察生成结论。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..budget import TokenBudgetTracker
from ..contracts.events import EventType
from ..contracts.strategies import (
    BaseStrategy,
    StrategyContext,
    StrategyResult,
    StrategyType,
    ToolCall,
)
from ..llm import LLMResult, LLMService, estimate_tokens
from ..tools import ToolRegistry, create_tool_registry

# 匹配 Markdown 代码块中的 JSON（```json ... ```），用于提取工具调用。
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_tool_call(text: str) -> dict[str, Any] | None:
    """从模型输出中解析 JSON 工具调用；解析失败按最终答案处理。

    依次尝试：代码块中的 JSON → 整段文本。取第一个含 "tool" 字段的 JSON 对象。

    Args:
        text: 模型输出文本。

    Returns:
        工具调用字典（含 tool/arguments）；解析不到时返回 None。
    """
    # 候选 1：所有 ```json 代码块内容。
    candidates = [match.group(1) for match in _JSON_FENCE.finditer(text)]
    # 候选 2：整段文本（兼容不带代码块的裸 JSON 输出）。
    candidates.append(text.strip())
    for candidate in candidates:
        # 截取第一个 { 到最后一个 } 之间的内容，容忍前后多余文本。
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            data = json.loads(candidate[start : end + 1])
        except ValueError:
            continue
        # 必须是含 "tool" 字段的字典才算工具调用。
        if isinstance(data, dict) and data.get("tool"):
            return data
    return None


def _estimate_messages(messages: list[dict[str, str]]) -> int:
    """估算整段对话历史的 token 数，用于预算校验。"""
    return estimate_tokens("".join(message.get("content", "") for message in messages))


@dataclass
class ReactNodeResult:
    """一次 React 节点循环的结构化产出，供顶层策略与 DAG 节点复用。"""

    output: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    rows: list[LLMResult] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)


class ReactStrategy(BaseStrategy):
    """React（Reasoning + Acting）策略：迭代调用工具并结合观察生成答案。"""

    def __init__(self, llm: LLMService, registry: ToolRegistry | None = None) -> None:
        """初始化 React 策略。
        Args:
            llm: LLM 服务。
            registry: 工具注册表；不传则使用默认注册表。
        """
        self._llm = llm
        self._registry = registry or create_tool_registry()

    @property
    def name(self) -> StrategyType:
        """返回策略类型标识。"""
        return StrategyType.REACT

    def _emit(self, context: StrategyContext, event_type: str, payload: dict[str, Any]) -> None:
        """向外部推送事件（若上下文配置了事件回调）。"""
        if context.emit is not None:
            context.emit(event_type, payload)

    @staticmethod
    def _with_node(
        payload: dict[str, Any],
        subtask_id: str | None,
        agent_role: str | None,
    ) -> dict[str, Any]:
        """给事件附加 DAG 节点归属；顶层 React 事件不携带 subtask_id。"""
        if subtask_id:
            payload["subtask_id"] = subtask_id
        if agent_role:
            payload["agent_role"] = agent_role
        return payload

    def _build_prompt(
        self,
        context: StrategyContext | None = None,
        tool_names: tuple[str, ...] | None = None,
        agent_role: str | None = None,
    ) -> str:
        """构造系统提示词：列出节点可用工具与工具调用格式。"""
        schemas = self._registry.list_schemas()
        if tool_names is not None:
            # DAG 节点可限制工具白名单，避免模型调用无关工具。
            schemas = [
                schema for schema in schemas
                if schema["name"] in tool_names
            ]
        schema_lines: list[str] = []
        # 把每个工具的名称、描述、参数 schema 拼成一行。
        for schema in schemas:
            schema_lines.append(
                f"- {schema['name']}: {schema['description']}，参数："
                f"{json.dumps(schema['parameters'], ensure_ascii=False)}"
            )
        prompt = (
            f"你是 Orchestra 多智能体编排框架中的 {agent_role or 'React 推理 Agent'}。\n"
            "需要外部信息时，只输出一个 JSON 工具调用，例如：\n"
            "{\"tool\": \"rag_search\", \"arguments\": {\"query\": \"报销标准\"}}\n"
            "可用工具：\n"
            + "\n".join(schema_lines)
            + "\n收到工具输出后，如信息不足可继续调用工具；不再需要工具时，直接生成最终答案。"
        )
        # P4 人事制度问答：强制先检索制度文档，保证回答有据可依。
        if context and context.context.get("scenario_id") == "hr_policy_qa":
            prompt += "\n\n当前场景为人事制度问答：必须首先调用 rag_search 检索制度文档，再基于工具结果生成答案。"
        return prompt

    async def execute(self, context: StrategyContext) -> StrategyResult:
        """执行顶层 React 策略：复用节点循环并落盘最终答案。"""
        tracker = TokenBudgetTracker(context.budget)
        node_result = await self.run_node(
            context,
            tracker,
            query=context.query,
            max_iterations=context.max_iterations,
        )
        await self._write_answer(context, node_result.output, node_result.trace)
        return self._build_result(
            node_result.output,
            node_result.tool_calls,
            node_result.rows,
        )

    async def run_node(
        self,
        context: StrategyContext,
        tracker: TokenBudgetTracker,
        *,
        query: str,
        subtask_id: str | None = None,
        agent_role: str | None = None,
        max_iterations: int | None = None,
        tool_names: tuple[str, ...] | None = None,
    ) -> ReactNodeResult:
        """执行一次可复用的 React 节点循环。

        Args:
            context: 执行上下文（工作区、事件回调、场景信息等）。
            tracker: 共享 Token 预算跟踪器，DAG 内所有节点累计到同一预算。
            query: 节点用户输入（DAG 节点为子任务目标与依赖结果）。
            subtask_id: 所属 DAG 子任务 ID；为 None 表示顶层 React 策略。
            agent_role: 节点角色名，用于事件归属与提示词。
            max_iterations: 最大迭代次数；为 None 使用上下文默认值。
            tool_names: 节点允许使用的工具；为 None 表示全部工具。

        Returns:
            ReactNodeResult：最终答案、工具调用、LLM 调用与轨迹摘要。
        """
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": self._build_prompt(context, tool_names, agent_role),
            },
            {"role": "user", "content": query},
        ]
        # 所有 LLM 调用结果，用于统计 token 用量。
        rows: list[LLMResult] = []
        # 工具调用记录，随结果返回供审计。
        tool_calls: list[ToolCall] = []
        # 执行轨迹摘要，写入工作区便于追溯。
        trace: list[str] = []
        final: str | None = None
        last_step = max_iterations or context.max_iterations

        # React 循环：最多迭代 max_iterations 次。
        for step in range(1, (max_iterations or context.max_iterations) + 1):
            last_step = step
            # 预算耗尽则提前终止。
            if tracker.remaining <= 0:
                break
            # 调用前校验剩余额度是否足够覆盖本次输入。
            input_estimate = _estimate_messages(messages)
            tracker.ensure_available(input_estimate)
            # 预算接近耗尽时自动切换到备用模型。
            model = tracker.choose_model(self._llm.default_model, self._llm.fallback_model)
            if model and self._llm.fallback_model and model == self._llm.fallback_model:
                # 发生模型降级时发出事件通知。
                self._emit(
                    context,
                    EventType.BUDGET_FALLBACK.value,
                    self._with_node({"step": step, "model": model}, subtask_id, agent_role),
                )
            self._emit(
                context,
                EventType.AGENT_STARTED.value,
                self._with_node({"step": step, "model": model}, subtask_id, agent_role),
            )
            # 按剩余额度收紧本次调用的输出上限。
            max_tokens = tracker.next_max_tokens(input_estimate)
            result = await self._llm.complete(messages, max_tokens=max_tokens, model=model)
            # 记录实际用量并推送 token 更新事件。
            tracker.record(result.input_tokens, result.output_tokens)
            rows.append(result)
            self._emit(
                context,
                EventType.TOKEN_UPDATED.value,
                self._with_node(
                    {"step": step, "token_usage": tracker.usage},
                    subtask_id,
                    agent_role,
                ),
            )
            # 把模型输出追加到对话历史。
            messages.append({"role": "assistant", "content": result.text})
            # 解析输出：是工具调用还是最终答案？
            call = parse_tool_call(result.text)
            if call is None:
                # 无工具调用 => 视为最终答案，直接结束循环。
                final = result.text
                break
            # 解析出工具调用：记录并执行。
            name = str(call.get("tool") or "")
            arguments = dict(call.get("arguments") or {})
            tool_calls.append(ToolCall(name=name, arguments=arguments))
            self._emit(
                context,
                EventType.TOOL_CALLED.value,
                self._with_node(
                    {"step": step, "tool": name, "arguments": arguments},
                    subtask_id,
                    agent_role,
                ),
            )
            # DAG 节点可声明工具白名单，减少模型误调工具。
            allowed = not tool_names or name in tool_names
            if not allowed:
                available = ", ".join(tool_names or ())
                output = f"工具不在当前节点可用列表：{name}，可用工具：{available}"
                success = False
            else:
                # 从注册表查找工具并执行，异常统一转为失败输出。
                tool = self._registry.get(name)
                if tool is None:
                    available = ", ".join(
                        schema["name"] for schema in self._registry.list_schemas()
                    )
                    output = f"工具不存在：{name}，可用工具：{available}"
                    success = False
                else:
                    try:
                        tool_result = await tool.run(arguments, context)
                        output = tool_result.output
                        success = tool_result.success
                    except Exception as exc:
                        output = f"工具执行异常：{exc}"
                        success = False
            self._emit(
                context,
                EventType.TOOL_COMPLETED.value,
                self._with_node(
                    {"step": step, "tool": name, "success": success},
                    subtask_id,
                    agent_role,
                ),
            )
            # 工具输出落盘到工作区，供追溯与复用；DAG 节点写入自身命名空间。
            if subtask_id:
                path = f"dag/{subtask_id}/step_{step:02d}_{name}.md"
            else:
                path = f"react/step_{step:02d}_{name}.md"
            await context.workspace.write(path, output)
            self._emit(
                context,
                EventType.WORKSPACE_UPDATED.value,
                self._with_node({"path": path}, subtask_id, agent_role),
            )
            # 记录轨迹摘要（截断到 200 字符）。
            trace.append(f"[{step}] 调用 {name}{arguments} -> {output[:200]}")
            # 把工具输出作为"观察"追加到对话历史，进入下一轮思考。
            messages.append({"role": "user", "content": f"工具输出({name}): {output}"})

        # 达到最大迭代次数仍未产出最终答案：基于已有观察生成结论。
        if final is None:
            final = self._build_without_answer(trace)
        self._emit(
            context,
            EventType.AGENT_COMPLETED.value,
            self._with_node(
                {
                    "step": last_step,
                    "model": rows[-1].model if rows else "unknown",
                },
                subtask_id,
                agent_role,
            ),
        )
        return ReactNodeResult(
            output=final,
            tool_calls=tool_calls,
            rows=rows,
            trace=trace,
        )

    async def _write_answer(self, context: StrategyContext, final: str, trace: list[str]) -> None:
        """把最终答案与执行轨迹写入工作区并推送事件。"""
        await context.workspace.write("answer.md", final)
        await context.workspace.write("react/trace.md", "\n".join(trace) or final)
        self._emit(context, EventType.WORKSPACE_UPDATED.value, {"path": "answer.md"})

    def _build_without_answer(self, trace: list[str]) -> str:
        """迭代耗尽时构造兜底结论：有观察则汇总观察，否则提示无结果。"""
        if trace:
            return "已达到最大迭代次数，结合已有工具观察生成结论：\n" + "\n".join(trace)
        return "已达到最大迭代次数，尚未获得可用工具结果。"

    def _build_result(
        self,
        output: str,
        tool_calls: list[ToolCall],
        rows: list[LLMResult],
    ) -> StrategyResult:
        """组装策略结果：答案 + 工具调用记录 + token 用量统计。"""
        return StrategyResult(
            output=output,
            tool_calls=tool_calls,
            token_usage={
                "input_tokens": sum(row.input_tokens for row in rows),
                "output_tokens": sum(row.output_tokens for row in rows),
                "calls": len(rows),
                "model": rows[-1].model if rows else "unknown",
            },
        )

