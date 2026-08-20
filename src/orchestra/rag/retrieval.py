"""RetrievalService：向量 + 关键词混合检索，可选 Rerank 精排。

设计要点：
- 向量召回按部门 Collection 隔离，保证人事/风控数据不串域；
- BM25 与向量使用 RRF（Reciprocal Rank Fusion）融合，避免量纲不一致；
- Rerank 失败时保留融合结果，不让外部服务故障阻断整条链路。
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Sequence

from ..contracts.rag import KnowledgeChunk, RetrievedChunk, RetrievalResult
from .departments import normalize_department
from .embeddings import EmbeddingProvider
from .rerank import RerankProvider
from .vector_store import ChromaVectorStore, VectorHit

_LATIN_TOKEN = re.compile(r"[a-z0-9_]+")
# RRF 常量 k=60，与业界常用配置保持一致。
_RRF_K = 60


def tokenize(text: str) -> list[str]:
    """对中文与英文混合文本做轻量词元化：英文词 + 中文二元词组。"""
    lowered = text.lower()
    tokens = _LATIN_TOKEN.findall(lowered)
    chinese_chars = [char for char in lowered if "\u4e00" <= char <= "\u9fff"]
    tokens.extend(
        "".join(chinese_chars[i : i + 2])
        for i in range(len(chinese_chars) - 1)
    )
    return [token for token in tokens if len(token) > 1]


class RetrievalService:
    """包 2 检索入口：向量/关键词/混合 + 可选 Rerank。"""

    def __init__(
        self,
        store: ChromaVectorStore,
        embeddings: EmbeddingProvider,
        reranker: RerankProvider | None = None,
        top_k: int = 5,
        mode: str = "hybrid",
        rerank_top_n: int = 5,
        min_score: float = 0.0,
    ) -> None:
        self._store = store
        self._embeddings = embeddings
        self._reranker = reranker
        self.top_k = top_k
        self.mode = mode
        self.rerank_top_n = rerank_top_n
        self.min_score = min_score

    async def search(
        self,
        query: str,
        department: str | None = None,
        top_k: int | None = None,
        mode: str | None = None,
    ) -> RetrievalResult:
        """执行混合检索并返回结构化结果。"""
        started = time.perf_counter()
        selected_mode = (mode or self.mode).lower()
        if selected_mode not in {"hybrid", "vector", "keyword"}:
            raise ValueError(f"不支持的检索模式：{selected_mode}")
        top_k = max(1, top_k or self.top_k)
        departments = (
            [normalize_department(department)]
            if department
            else self._store.list_departments()
        )

        vector_hits: list[VectorHit] = []
        if selected_mode != "keyword":
            query_embedding = (await self._embeddings.embed([query]))[0]
            vector_hits = self._store.query(
                query_embedding,
                departments=departments,
                n_results=max(top_k * 4, 10),
            )

        candidates: list[tuple[KnowledgeChunk, float]] = []
        if selected_mode == "vector":
            candidates = self._normalize_vector_hits(vector_hits)
        else:
            keyword_rank = self._keyword_rank(query, self._store.get_all_chunks(departments))
            if selected_mode == "keyword":
                candidates = keyword_rank
            else:
                candidates = self._fuse(vector_hits, keyword_rank)

        candidates = [(chunk, score) for chunk, score in candidates if score >= self.min_score]

        reranked = False
        if self._reranker is not None and candidates:
            try:
                docs = [chunk.content for chunk, _ in candidates[: self.rerank_top_n * 4]]
                reordered = await self._reranker.rerank(query, docs, top_n=self.rerank_top_n)
                if reordered:
                    rescored: list[tuple[KnowledgeChunk, float]] = []
                    for index, score in reordered:
                        if index < len(candidates):
                            rescored.append((candidates[index][0], score))
                    if rescored:
                        candidates = rescored
                        reranked = True
            except Exception:
                # Rerank 只是精排增强，失败时保留向量/关键词融合结果。
                pass

        hits_tuple = tuple(
            RetrievedChunk(chunk=chunk, score=round(float(score), 4), rank=index)
            for index, (chunk, score) in enumerate(candidates[:top_k], start=1)
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        return RetrievalResult(
            query=query,
            hits=hits_tuple,
            mode=selected_mode,
            latency_ms=latency_ms,
            reranked=reranked,
            confidence=hits_tuple[0].score if hits_tuple else 0.0,
        )

    @staticmethod
    def _normalize_vector_hits(hits: Sequence[VectorHit]) -> list[tuple[KnowledgeChunk, float]]:
        """把向量距离转换为 0~1 相似度，并做最小最大归一化。"""
        similarities = [1.0 - hit.distance for hit in hits]
        if not similarities:
            return []
        min_score = min(similarities)
        max_score = max(similarities)
        candidates: list[tuple[KnowledgeChunk, float]] = []
        for hit, similarity in zip(hits, similarities):
            if max_score > min_score:
                normalized = (similarity - min_score) / (max_score - min_score)
            else:
                normalized = 1.0 if similarity > 0 else 0.0
            candidates.append((hit.chunk, normalized))
        return candidates

    @staticmethod
    def _keyword_rank(
        query: str,
        chunks: Sequence[KnowledgeChunk],
    ) -> list[tuple[KnowledgeChunk, float]]:
        """BM25 关键词检索；rank-bm25 未安装时退化为词元重叠计分。"""
        if not chunks:
            return []
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return []
        try:
            from rank_bm25 import BM25Okapi

            corpus = [tokenize(chunk.content) for chunk in chunks]
            bm25 = BM25Okapi(corpus=corpus)
            scores = bm25.get_scores(list(query_tokens))
        except ImportError:
            scores = []
            for chunk in chunks:
                doc_tokens = set(tokenize(chunk.content))
                scores.append(float(len(query_tokens & doc_tokens)))
        ranked = sorted(
            ((chunk, float(score)) for chunk, score in zip(chunks, scores) if score > 0),
            key=lambda pair: pair[1],
            reverse=True,
        )
        max_score = ranked[0][1] if ranked else 1.0
        return [(chunk, score / max_score if max_score else 0.0) for chunk, score in ranked]

    @staticmethod
    def _fuse(
        vector_hits: Sequence[VectorHit],
        keyword_rank: Sequence[tuple[KnowledgeChunk, float]],
    ) -> list[tuple[KnowledgeChunk, float]]:
        """RRF 融合：向量与关键词各自按名次贡献分数，再按总分排序。"""
        rrf_scores: dict[str, float] = defaultdict(float)
        chunks_by_id: dict[str, KnowledgeChunk] = {}
        for rank, hit in enumerate(vector_hits, start=1):
            rrf_scores[hit.chunk.chunk_id] += 1.0 / (_RRF_K + rank)
            chunks_by_id[hit.chunk.chunk_id] = hit.chunk
        for rank, (chunk, _) in enumerate(keyword_rank, start=1):
            rrf_scores[chunk.chunk_id] += 1.0 / (_RRF_K + rank)
            chunks_by_id[chunk.chunk_id] = chunk
        ranked = sorted(
            ((chunk_id, score) for chunk_id, score in rrf_scores.items()),
            key=lambda pair: pair[1],
            reverse=True,
        )
        max_score = ranked[0][1] if ranked else 1.0
        return [
            (chunks_by_id[chunk_id], score / max_score if max_score else 0.0)
            for chunk_id, score in ranked
            if chunk_id in chunks_by_id
        ]
