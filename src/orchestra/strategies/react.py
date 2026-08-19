"""React 策略：模型在"思考-工具调用-观察"循环中完成多步任务。

执行流程：
1. 模型输出 JSON 工具调用 → 解析并执行工具；
2. 把工具输出作为"观察"追加到对话历史；
3. 模型不再输出工具调用时，生成最终答案；
4. 达到最大迭代次数仍未完成时，基于已有观察生成结论。
"""
from __future__ import annotations

import json
import re
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

    def _build_prompt(self, context: StrategyContext | None = None) -> str:
        """构造系统提示词：说明工具调用格式并列出可用工具。"""
        schema_lines: list[str] = []
        # 把每个工具的名称、描述、参数 schema 拼成一行。
        for schema in self._registry.list_schemas():
            schema_lines.append(
                f"- {schema['name']}: {schema['description']}，参数："
                f"{json.dumps(schema['parameters'], ensure_ascii=False)}"
            )
        prompt = (
            "你是 Orchestra 多智能体编排框架中的 React 推理 Agent。\n"
            "需要外部信息时，只输出一个 JSON 工具调用，例如：\n"
            '{"tool": "rag_search", "arguments": {"query": "报销标准"}}\n'
            "可用工具：\n"
            + "\n".join(schema_lines)
            + "\n收到工具输出后，不再调用工具，直接生成最终答案。"
        )
        # P4 人事制度问答：强制先检索制度文档，保证回答有据可依。
        if context and context.context.get("scenario_id") == "hr_policy_qa":
            prompt += "\n\n当前场景为人事制度问答：必须首先调用 rag_search 检索制度文档，再基于工具结果生成答案。"
        return prompt

    async def execute(self, context: StrategyContext) -> StrategyResult:
        """执行 React 循环：思考 → 工具调用 → 观察，直到产出最终答案。

        Args:
            context: 策略执行上下文（查询、预算、工作区、事件回调等）。

        Returns:
            策略结果：最终答案、工具调用记录与 token 用量。
        """
        # 预算跟踪器：累计用量、动态收紧 max_tokens、预算不足时降级模型。
        tracker = TokenBudgetTracker(context.budget)
        # 对话历史：系统提示 + 用户问题，后续逐步追加助手输出与工具观察。
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._build_prompt(context)},
            {"role": "user", "content": context.query},
        ]
        # 所有 LLM 调用结果，用于统计 token 用量。
        rows: list[LLMResult] = []
        # 工具调用记录，随结果返回供审计。
        tool_calls: list[ToolCall] = []
        # 执行轨迹摘要，写入工作区便于追溯。
        trace: list[str] = []
        
        # React 循环：最多迭代 max_iterations 次。
        for step in range(1, context.max_iterations + 1):
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
                self._emit(context, EventType.BUDGET_FALLBACK.value, {"step": step, "model": model})
            self._emit(context, EventType.AGENT_STARTED.value, {"step": step, "model": model})
            # 按剩余额度收紧本次调用的输出上限。
            max_tokens = tracker.next_max_tokens(input_estimate)
            result = await self._llm.complete(messages, max_tokens=max_tokens, model=model)
            # 记录实际用量并推送 token 更新事件。
            tracker.record(result.input_tokens, result.output_tokens)
            rows.append(result)
            self._emit(context, EventType.TOKEN_UPDATED.value, {"step": step, "token_usage": tracker.usage})
            # 把模型输出追加到对话历史。
            messages.append({"role": "assistant", "content": result.text})
            # 解析输出：是工具调用还是最终答案？
            call = parse_tool_call(result.text)
            if call is None:
                # 无工具调用 => 视为最终答案，写入工作区并返回。
                await self._write_answer(context, result.text, trace)
                return self._build_result(result.text, tool_calls, rows)
            # 解析出工具调用：记录并执行。
            name = str(call.get("tool") or "")
            arguments = dict(call.get("arguments") or {})
            tool_calls.append(ToolCall(name=name, arguments=arguments))
            self._emit(
                context,
                EventType.TOOL_CALLED.value,
                {"step": step, "tool": name, "arguments": arguments},
            )
            # 从注册表查找工具并执行，异常统一转为失败输出。
            tool = self._registry.get(name)
            if tool is None:
                # 工具不存在：给出可用工具列表作为观察。
                available = ", ".join(schema["name"] for schema in self._registry.list_schemas())
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
                {"step": step, "tool": name, "success": success},
            )
            # 工具输出落盘到工作区，供追溯与复用。
            path = f"react/step_{step:02d}_{name}.md"
            await context.workspace.write(path, output)
            self._emit(context, EventType.WORKSPACE_UPDATED.value, {"path": path})
            # 记录轨迹摘要（截断到 200 字符）。
            trace.append(f"[{step}] 调用 {name}{arguments} -> {output[:200]}")
            # 把工具输出作为"观察"追加到对话历史，进入下一轮思考。
            messages.append({"role": "user", "content": f"工具输出({name}): {output}"})
        # 达到最大迭代次数仍未产出最终答案：基于已有观察生成结论。
        final = self._build_without_answer(trace)
        await self._write_answer(context, final, trace)
        return self._build_result(final, tool_calls, rows)

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
