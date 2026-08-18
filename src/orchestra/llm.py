"""LLM Provider 抽象：Mock 用于开发与测试，OpenAI 兼容接口用于真实调用。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# 中文按约 4 字符估算 1 token，只用于预算与统计，不追求精确。
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = "mock"


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        ...


# 确定性 Mock：无网络依赖，方便测试与演示。
class MockLLMProvider:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        user_text = " | ".join(m.get("content", "") for m in messages if m.get("role") == "user")
        text = f"[Mock] {user_text}"
        return LLMResult(
            text=text,
            input_tokens=estimate_tokens("".join(m.get("content", "") for m in messages)),
            output_tokens=estimate_tokens(text),
            model=model or "mock",
        )


class OpenAICompatProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when ORCHESTRA_LLM_PROVIDER=openai")
        self.api_key = api_key
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
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        text = (data["choices"][0]["message"].get("content") or "").strip()
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            input_tokens=usage.get("prompt_tokens") or estimate_tokens("".join(m.get("content", "") for m in messages)),
            output_tokens=usage.get("completion_tokens") or estimate_tokens(text),
            model=payload["model"],
        )


class LLMService:
    def __init__(
        self,
        provider: LLMProvider,
        default_model: str,
        fallback_model: str | None = None,
    ) -> None:
        self.provider = provider
        self.default_model = default_model
        self.fallback_model = fallback_model

    async def complete(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> LLMResult:
        # 主模型失败时降级到备用模型，只重试一次。
        try:
            return await self.provider.complete(messages, model=self.default_model, max_tokens=max_tokens)
        except Exception:
            if self.fallback_model:
                return await self.provider.complete(messages, model=self.fallback_model, max_tokens=max_tokens)
            raise

def create_llm_provider(settings):
    if settings.llm_provider.lower() == "openai":
        return OpenAICompatProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.llm_model,
        )
    return MockLLMProvider()