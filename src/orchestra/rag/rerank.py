"""Rerank Provider：调用 DashScope / MaaS 的 text-rerank 服务。

Rerank 在向量/关键词融合后再做一次精排，改善长文档 Top-N 命中质量。
"""
from __future__ import annotations


class RerankProvider:
    """DashScope text-rerank 兼容调用：POST 到完整服务地址。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "gte-rerank-v2",
        top_n: int = 5,
    ) -> None:
        if not api_key or not base_url:
            raise ValueError("Rerank API Key / Base URL 未配置")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.top_n = top_n

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list[tuple[int, float]]:
        """返回按相关性降序的 (原文索引, 分数) 列表。"""
        if not documents:
            return []
        import httpx

        payload = {
            "model": self.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"top_n": top_n or self.top_n},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        output = data.get("output") if isinstance(data.get("output"), dict) else data
        raw_results = output.get("results") or output.get("data") or []
        scored: list[tuple[int, float]] = []
        for index, item in enumerate(raw_results):
            if not isinstance(item, dict):
                continue
            original_index = int(item.get("index", index))
            score = float(
                item.get("relevance_score")
                or item.get("score")
                or 0.0
            )
            scored.append((original_index, score))
        return sorted(scored, key=lambda pair: pair[1], reverse=True)
