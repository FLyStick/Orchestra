"""Embedding Provider：OpenAI 兼容接口与本地 sentence-transformers 两种实现。

包 2 默认使用 OpenAI 兼容接口（兼容 DashScope 等服务），
本地模型作为离线/内网部署选项，按需安装 sentence-transformers。
"""
from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding Provider 协议，所有实现需异步提供向量化能力。"""

    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """把多段文本转换为等长向量列表。"""


class OpenAICompatEmbeddingProvider:
    """OpenAI 兼容 Embedding 服务：POST /embeddings，支持 DashScope 等。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "text-embedding-3-small",
        batch_size: int = 16,
    ) -> None:
        if not api_key:
            raise ValueError("Embedding API Key 未配置")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.batch_size = batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """分批调用 /embeddings，按返回 index 恢复原始顺序。"""
        if not texts:
            return []
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}"}
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            payload = {"model": self.model, "input": batch}
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
            raw_items = data.get("data") or data.get("embeddings") or []
            if not raw_items:
                raise RuntimeError("Embedding 服务未返回向量数据")
            indexed: list[tuple[int, list[float]]] = []
            for index, item in enumerate(raw_items):
                if isinstance(item, dict):
                    vector = item.get("embedding") or item.get("vector")
                    indexed.append((int(item.get("index", index)), [float(v) for v in vector]))
                else:
                    indexed.append((index, [float(v) for v in item]))
            indexed.sort(key=lambda pair: pair[0])
            embeddings.extend(vector for _, vector in indexed)
        return embeddings


class LocalEmbeddingProvider:
    """本地 sentence-transformers 模型，首次调用时懒加载模型。"""

    def __init__(self, model_name: str) -> None:
        self.model = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model)
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """编码可能阻塞事件循环，放到线程池执行。"""
        if not texts:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._encode, texts)


def create_embedding_provider(settings) -> EmbeddingProvider | None:
    """按配置创建 Embedding Provider；缺少 Key 时返回 None。"""
    provider = str(getattr(settings, "embedding_provider", "") or "").lower()
    if provider in {"openai", "dashscope", "aliyun"}:
        if not settings.embedding_api_key:
            return None
        return OpenAICompatEmbeddingProvider(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
        )
    if provider == "local":
        return LocalEmbeddingProvider(settings.embedding_model)
    return None
