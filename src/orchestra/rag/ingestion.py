"""IngestionService：文档解析、分块、Embedding 与向量化入库。

流程：扫描 data/knowledge/{department} → parse_document → split_text →
EmbeddingProvider → ChromaVectorStore → ManifestStore 落文档级状态。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..contracts.rag import DocumentRecord, IndexedChunk, KnowledgeChunk
from .chunking import split_text
from .departments import normalize_department
from .embeddings import EmbeddingProvider
from .manifest import ManifestStore
from .parsing import ParsedSection, parse_document
from .vector_store import ChromaVectorStore


class IngestionService:
    """文档导入与索引服务，提供单文件/目录/演示知识库三种入口。"""

    def __init__(
        self,
        source_dir: str | Path,
        vector_store: ChromaVectorStore,
        embeddings: EmbeddingProvider,
        manifest_path: str | Path,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> None:
        self.source_dir = Path(source_dir).resolve()
        self._store = vector_store
        self._embeddings = embeddings
        self._manifest = ManifestStore(Path(manifest_path))
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _department_from_path(self, path: Path) -> str:
        relative = path.resolve().relative_to(self.source_dir)
        return normalize_department(relative.parts[0])

    def _extract_title(self, path: Path, sections: list[ParsedSection]) -> str:
        """优先取一级 Markdown 标题，其次取首行，最后用文件名。"""
        for section in sections:
            for line in section.text.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    return stripped[2:].strip()[:100] or path.stem
                if stripped:
                    return stripped[:100]
        return path.stem

    async def index_file(
        self,
        path: str | Path,
        department: str | None = None,
        title: str | None = None,
    ) -> DocumentRecord:
        """解析单个文件并写入向量库与文档清单。"""
        file_path = Path(path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"文档不存在：{file_path}")
        department = normalize_department(department or self._department_from_path(file_path))
        relative = file_path.relative_to(self.source_dir)
        source = relative.as_posix()
        raw_bytes = file_path.read_bytes()
        version = hashlib.sha256(raw_bytes).hexdigest()[:16]
        document_id = hashlib.sha256(f"{department}:{source}:{version}".encode("utf-8")).hexdigest()[:16]
        sections = parse_document(file_path)
        resolved_title = title or self._extract_title(file_path, sections)

        # 同一来源重复导入时先清理旧块，避免新旧版本共存造成重复证据。
        self._store.delete_source(source, department)

        pending: list[IndexedChunk] = []
        chunk_index = 0
        for section in sections:
            for text in split_text(section.text, self.chunk_size, self.chunk_overlap):
                metadata: dict[str, Any] = {
                    "document_id": document_id,
                    "source": source,
                    "department": department,
                    "title": resolved_title,
                    "version": version,
                }
                if section.page is not None:
                    metadata["page"] = section.page
                chunk = KnowledgeChunk(
                    chunk_id=f"{document_id}:{chunk_index:04d}",
                    document_id=document_id,
                    source=source,
                    department=department,
                    content=text,
                    title=resolved_title,
                    page=section.page,
                    version=version,
                    metadata=metadata,
                )
                pending.append(IndexedChunk(chunk=chunk))
                chunk_index += 1

        if not pending:
            raise ValueError(f"文档未提取到可索引文本：{file_path}")

        vectors = await self._embeddings.embed([item.chunk.content for item in pending])
        if len(vectors) != len(pending):
            raise RuntimeError("Embedding 返回数量与知识块数量不一致")
        for item, vector in zip(pending, vectors):
            item.embedding = tuple(vector)
        self._store.upsert_indexed_chunks(pending)

        record = DocumentRecord(
            document_id=document_id,
            source=source,
            department=department,
            title=resolved_title,
            version=version,
            file_path=relative.as_posix(),
            chunk_count=len(pending),
            indexed_at=self._now(),
        )
        self._manifest.upsert(record)
        return record

    async def index_directory(
        self,
        department: str | None = None,
    ) -> tuple[list[DocumentRecord], list[str]]:
        """扫描知识目录并索引全部支持的文件；单个文件失败不阻断其余文件。"""
        if department:
            directories = [self.source_dir / normalize_department(department)]
        else:
            directories = [
                child for child in self.source_dir.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            ] if self.source_dir.exists() else []

        records: list[DocumentRecord] = []
        errors: list[str] = []
        for directory in sorted(directories):
            for file_path in sorted(directory.rglob("*")):
                if not file_path.is_file() or file_path.suffix.lower() not in {
                    ".md", ".markdown", ".txt", ".text", ".pdf", ".docx", ".xlsx", ".xlsm", ".pptx",
                }:
                    continue
                try:
                    records.append(await self.index_file(file_path))
                except Exception as exc:
                    errors.append(f"{file_path}: {exc}")
        return records, errors

    async def seed_demo(
        self,
        department: str | None = None,
    ) -> tuple[list[DocumentRecord], list[str]]:
        """把内置 P4 演示知识库写为 data/knowledge 下的 Markdown 并索引。"""
        from ..knowledge import KNOWLEDGE_DOCS

        for doc in KNOWLEDGE_DOCS:
            target = self.source_dir / doc.source
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{doc.content}\n", encoding="utf-8")
        return await self.index_directory(department)

    def delete(self, document_id: str) -> bool:
        """删除清单记录与向量块，返回是否找到并删除。"""
        record = self._manifest.get(document_id)
        if record is None:
            return False
        self._store.delete_source(str(record["source"]), str(record["department"]))
        self._manifest.remove(document_id)
        return True

    def list_documents(self, department: str | None = None) -> list[dict[str, Any]]:
        """返回文档清单，可按部门筛选。"""
        return self._manifest.list(normalize_department(department) if department else None)
