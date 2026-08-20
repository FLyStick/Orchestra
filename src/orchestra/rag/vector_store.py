"""ChromaDB 向量存储：本地持久化或 Server 模式，按部门分 Collection。

ChromaVectorStore 只负责 chunk 级 upsert/query/delete，
具体 Embedding 计算由上层 RetrievalService / IngestionService 完成。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..contracts.rag import IndexedChunk, KnowledgeChunk


@dataclass(frozen=True)
class VectorHit:
    """向量检索原始命中：知识块 + 距离（越低越相似）。"""

    chunk: KnowledgeChunk
    distance: float


class ChromaVectorStore:
    """ChromaDB 封装，按部门使用独立 Collection 实现数据隔离。"""

    def __init__(
        self,
        path: str | Path | None = None,
        host: str | None = None,
        port: int = 8001,
        collection_prefix: str = "orchestra",
    ) -> None:
        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover - 依赖未安装时给出明确提示
            raise RuntimeError("ChromaDB 未安装，请先执行 pip install chromadb") from exc
        self._chromadb = chromadb
        self.prefix = collection_prefix
        if host:
            self._client = chromadb.HttpClient(host=host, port=int(port))
        else:
            data_dir = Path(path or "data/chroma")
            data_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(data_dir))

    def _collection_name(self, department: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(department).lower())
        return f"{self.prefix}_{safe}"

    def _get_collection(self, department: str):
        return self._client.get_or_create_collection(
            name=self._collection_name(department),
            metadata={"hnsw:space": "cosine"},
        )

    def _get_existing_collection(self, department: str):
        name = self._collection_name(department)
        try:
            return self._client.get_collection(name=name)
        except Exception:
            return None

    def list_departments(self) -> list[str]:
        """列出已有非空 Collection 对应的部门标识。"""
        prefix = f"{self.prefix}_"
        departments: list[str] = []
        try:
            collections = self._client.list_collections()
        except Exception:
            return departments
        for collection in collections:
            name = collection if isinstance(collection, str) else getattr(collection, "name", str(collection))
            if name.startswith(prefix):
                departments.append(name[len(prefix):])
        return sorted(set(departments))

    @staticmethod
    def _metadata(chunk: KnowledgeChunk) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "document_id": chunk.document_id,
            "source": chunk.source,
            "department": chunk.department,
            "title": chunk.title,
            "version": chunk.version,
        }
        if chunk.page is not None:
            metadata["page"] = chunk.page
        return metadata

    def upsert_indexed_chunks(self, items: Sequence[IndexedChunk]) -> None:
        """批量写入知识块：按部门分桶后写入对应 Collection。"""
        by_department: dict[str, list[IndexedChunk]] = {}
        for item in items:
            by_department.setdefault(item.chunk.department, []).append(item)

        for department, department_items in by_department.items():
            collection = self._get_collection(department)
            collection.upsert(
                ids=[item.chunk.chunk_id for item in department_items],
                embeddings=[list(item.embedding) for item in department_items],
                documents=[item.chunk.content for item in department_items],
                metadatas=[self._metadata(item.chunk) for item in department_items],
            )

    @staticmethod
    def _chunk_from_data(
        chunk_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> KnowledgeChunk:
        metadata = dict(metadata or {})
        page = metadata.get("page")
        return KnowledgeChunk(
            chunk_id=chunk_id,
            document_id=str(metadata.get("document_id") or ""),
            source=str(metadata.get("source") or ""),
            department=str(metadata.get("department") or ""),
            content=content or "",
            title=str(metadata.get("title") or ""),
            page=int(page) if page is not None else None,
            version=str(metadata.get("version") or ""),
            metadata=metadata,
        )

    def query(
        self,
        query_embedding: Sequence[float],
        departments: Sequence[str] | None = None,
        n_results: int = 10,
    ) -> list[VectorHit]:
        """按部门查询最近邻，返回按距离升序的 VectorHit 列表。"""
        departments = list(departments) if departments else self.list_departments()
        hits: list[VectorHit] = []
        for department in departments:
            collection = self._get_existing_collection(department)
            if collection is None:
                continue
            result = collection.query(
                query_embeddings=[list(query_embedding)],
                n_results=max(1, n_results),
                include=["documents", "metadatas", "distances"],
            )
            ids = (result.get("ids") or [[]])[0]
            documents = (result.get("documents") or [[]])[0]
            metadatas = (result.get("metadatas") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
            for chunk_id, content, metadata, distance in zip(ids, documents, metadatas, distances):
                hits.append(
                    VectorHit(
                        chunk=self._chunk_from_data(chunk_id, content or "", metadata or {}),
                        distance=float(distance),
                    )
                )
        return sorted(hits, key=lambda hit: hit.distance)[: n_results]

    def get_all_chunks(self, departments: Sequence[str] | None = None) -> list[KnowledgeChunk]:
        """读取指定部门（缺省为全部）的全部知识块，供 BM25/关键词检索使用。"""
        departments = list(departments) if departments else self.list_departments()
        chunks: list[KnowledgeChunk] = []
        for department in departments:
            collection = self._get_existing_collection(department)
            if collection is None:
                continue
            data = collection.get(include=["documents", "metadatas"])
            ids = data.get("ids") or []
            documents = data.get("documents") or []
            metadatas = data.get("metadatas") or []
            for chunk_id, content, metadata in zip(ids, documents, metadatas):
                chunks.append(self._chunk_from_data(chunk_id, content or "", metadata or {}))
        return chunks

    def delete_source(self, source: str, department: str) -> None:
        """按来源删除某个文档的全部旧块，用于重复导入时先清后写。"""
        collection = self._get_existing_collection(department)
        if collection is not None:
            collection.delete(where={"source": source})

    def delete_document(self, document_id: str, department: str) -> None:
        """按文档 ID 删除向量块。"""
        collection = self._get_existing_collection(department)
        if collection is not None:
            collection.delete(where={"document_id": document_id})
