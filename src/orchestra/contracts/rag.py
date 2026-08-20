"""RAG 契约：知识块、检索结果、索引结果与文档记录。

包 2 的向量检索、混合检索与文档管理共用这些数据结构，
避免 IngestionService / RetrievalService / API 之间各自维护格式。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KnowledgeChunk:
    """一段写入向量库的最小知识单元。"""

    chunk_id: str
    document_id: str
    source: str
    department: str
    content: str
    title: str = ""
    page: int | None = None
    version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source": self.source,
            "department": self.department,
            "content": self.content,
            "title": self.title,
            "page": self.page,
            "version": self.version,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RetrievedChunk:
    """一条检索结果：知识块 + 排序分数 + 名次。"""

    chunk: KnowledgeChunk
    score: float = 0.0
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "rank": self.rank, "chunk": self.chunk.to_dict()}


@dataclass(frozen=True)
class RetrievalResult:
    """一次检索的完整结果，保留模式、耗时与置信度供可观测。"""

    query: str
    hits: tuple[RetrievedChunk, ...] = ()
    mode: str = "hybrid"
    latency_ms: int = 0
    reranked: bool = False
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "hits": [hit.to_dict() for hit in self.hits],
            "mode": self.mode,
            "latency_ms": self.latency_ms,
            "reranked": self.reranked,
            "confidence": self.confidence,
        }


@dataclass
class IndexedChunk:
    """待写入向量库的知识块与其 Embedding 向量。"""

    chunk: KnowledgeChunk
    embedding: tuple[float, ...] = ()


@dataclass
class DocumentRecord:
    """文档索引记录：来源、版本、块数与索引状态。"""

    document_id: str
    source: str
    department: str
    title: str
    version: str
    file_path: str
    chunk_count: int
    indexed_at: str
    status: str = "indexed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source": self.source,
            "department": self.department,
            "title": self.title,
            "version": self.version,
            "file_path": self.file_path,
            "chunk_count": self.chunk_count,
            "indexed_at": self.indexed_at,
            "status": self.status,
        }
