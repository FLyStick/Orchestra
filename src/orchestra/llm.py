"""LLM Provider 抽象：Mock 用于开发与测试，OpenAI 兼容接口用于真实调用。

通过统一的 LLMProvider 协议屏蔽底层差异，上层（策略层）只依赖
LLMService.complete 接口，不关心具体是 mock 还是真实模型。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# 中文按约 4 字符估算 1 token，只用于预算与统计，不追求精确。
def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数（中文约 4 字符 ≈ 1 token）。
    Args:
        text: 待估算的文本。
    Returns:
        估算的 token 数，至少为 1。
    """
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class LLMResult:
    """一次 LLM 调用的结果：输出文本与用量统计。"""
    text: str  # 模型生成的文本。
    input_tokens: int = 0  # 输入 token 数。
    output_tokens: int = 0  # 输出 token 数。
    model: str = "mock"  # 实际使用的模型名。


@runtime_checkable
class LLMProvider(Protocol):
    """LLM Provider 协议：所有 Provider 需实现 complete 方法。"""
    
    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        ...


# 确定性 Mock：无网络依赖，方便测试与演示。
class MockLLMProvider:
    """Mock Provider：不发起网络请求，返回确定性输出。

    用于开发调试、单元测试与演示，避免消耗真实 token 费用。
    支持 delay 参数模拟网络延迟，以及 RAG 触发词模拟工具调用流程。
    """

    # 演示触发词：包含该短语时，Mock 会模拟一次 React 工具调用。
    _RAG_TRIGGER = "调用rag_search"

    def __init__(self, delay: float = 0.0) -> None:
        """初始化 Mock Provider。

        Args:
            delay: 每次调用前模拟的网络延迟秒数（默认 0，即不延迟）。
        """
        self.delay = delay

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        # 可配置延迟，用于测试超时/并发场景。
        if self.delay:
            await asyncio.sleep(self.delay)
        # 拼接所有 user 消息作为"模型输出"内容。
        user_text = " | ".join(m.get("content", "") for m in messages if m.get("role") == "user")
        if self._RAG_TRIGGER in user_text:
            # 第一次输出 JSON 工具调用，收到工具结果后输出最终答案。
            if "工具输出(rag_search)" not in user_text:
                # 提取触发词后的内容作为检索 query（截断到 20 字符）。
                rest = user_text.split(self._RAG_TRIGGER, 1)[-1].strip(" ，。").strip()[:20]
                # 模拟模型输出工具调用 JSON（React 循环第一步）。
                text = json.dumps(
                    {"tool": "rag_search", "arguments": {"query": rest or "报销标准"}},
                    ensure_ascii=False,
                )
            else:
                # 消息中已包含工具输出，模拟模型基于工具结果生成最终答案。
                text = "[Mock] 已根据 rag_search 工具结果完成回答，结论以检索到的制度文档为准。"
        else:
            # 普通场景：直接回显用户输入。
            text = f"[Mock] {user_text}"
        return LLMResult(
            text=text,
            input_tokens=estimate_tokens("".join(m.get("content", "") for m in messages)),
            output_tokens=estimate_tokens(text),
            model=model or "mock",
        )


class OpenAICompatProvider:
    """OpenAI 兼容 Provider：通过 HTTP 调用真实 LLM 服务。

    兼容任何实现 /chat/completions 协议的服务（OpenAI、DashScope 等），
    通过 base_url 切换服务商。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
    ) -> None:
        """初始化 OpenAI 兼容 Provider。
        Args:
            api_key: API 密钥，必填。
            base_url: 服务基础地址（不含 /chat/completions 后缀）。
            model: 默认模型名。
        Raises:
            ValueError: api_key 为空时抛出。
        """
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when ORCHESTRA_LLM_PROVIDER=openai")
        self.api_key = api_key
        # 去掉末尾斜杠，避免拼接 URL 时出现双斜杠。
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        import httpx

        # 拼装 OpenAI 兼容的 chat/completions 请求。
        payload: dict[str, object] = {"model": model or self.model, "messages": messages}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # 60 秒超时，防止模型响应过慢挂起请求。
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            # 非 2xx 状态码直接抛异常，由上层 LLMService 处理降级。
            response.raise_for_status()
            data = response.json()
        # 提取模型输出文本（去掉首尾空白）。
        text = (data["choices"][0]["message"].get("content") or "").strip()
        # 优先使用 API 返回的精确用量；缺失时回退到本地估算。
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            input_tokens=usage.get("prompt_tokens") or estimate_tokens("".join(m.get("content", "") for m in messages)),
            output_tokens=usage.get("completion_tokens") or estimate_tokens(text),
            model=payload["model"],
        )


class LLMService:
    """LLM 服务门面：统一调用入口，内置主/备模型降级逻辑。"""

    def __init__(
        self,
        provider: LLMProvider,
        default_model: str,
        fallback_model: str | None = None,
    ) -> None:
        """初始化 LLM 服务。

        Args:
            provider: 底层 Provider（mock 或 openai 兼容）。
            default_model: 主模型名。
            fallback_model: 备用模型名；None 表示不降级。
        """
        self.provider = provider
        self.default_model = default_model
        self.fallback_model = fallback_model

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResult:
        # 主模型失败时降级到备用模型，只重试一次；显式指定备用模型时不再重复降级。
        primary = model or self.default_model
        try:
            return await self.provider.complete(messages, model=primary, max_tokens=max_tokens)
        except Exception:
            # 有备用模型且当前不是备用模型时，用备用模型重试一次。
            if self.fallback_model and model != self.fallback_model:
                return await self.provider.complete(messages, model=self.fallback_model, max_tokens=max_tokens)
            raise

def create_llm_provider(settings):
    """根据配置创建对应的 LLM Provider。

    Args:
        settings: 运行配置（llm_provider、api_key、base_url、model）。

    Returns:
        openai 配置时返回 OpenAICompatProvider，否则返回 MockLLMProvider。
    """
    if settings.llm_provider.lower() == "openai":
        return OpenAICompatProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.llm_model,
        )
    return MockLLMProvider()
