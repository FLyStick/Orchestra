"""包 2 RAG 测试：分块、文档导入、混合检索与 RAG 工具写入工作区。"""
import asyncio
import hashlib
import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

_has_chroma = importlib.util.find_spec("chromadb") is not None


def _tokenize(text: str) -> list[str]:
    """测试用轻量 tokenizer：英文词 + 中文二元组。"""
    lowered = text.lower()
    tokens = []
    import re
    tokens.extend(re.findall(r"[a-z0-9_]+", lowered))
    chars = [ch for ch in lowered if "\u4e00" <= ch <= "\u9fff"]
    tokens.extend("".join(chars[i : i + 2]) for i in range(len(chars) - 1))
    return [token for token in tokens if len(token) > 1]


class FakeEmbeddingProvider:
    """确定性伪 Embedding：按词元哈希写稀疏向量，避免真实网络调用。"""

    model = "fake"

    def __init__(self, dim: int = 128) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in _tokenize(text):
                index = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dim
                vector[index] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class ChunkingTest(unittest.TestCase):
    def test_split_text_respects_size_and_overlap(self) -> None:
        from orchestra.rag.chunking import split_text

        text = "第一段内容。" * 200
        chunks = split_text(text, chunk_size=80, chunk_overlap=10)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))
        self.assertIn(text[:10], chunks[0])


@unittest.skipUnless(_has_chroma, "chromadb not installed")
class RagRoundTripTest(unittest.TestCase):
    def _build_stack(self, root: Path):
        from orchestra.rag.ingestion import IngestionService
        from orchestra.rag.retrieval import RetrievalService
        from orchestra.rag.vector_store import ChromaVectorStore

        source_dir = root / "knowledge"
        source_dir.mkdir(parents=True, exist_ok=True)
        store = ChromaVectorStore(path=str(root / "chroma"))
        embeddings = FakeEmbeddingProvider()
        retrieval = RetrievalService(store=store, embeddings=embeddings, top_k=5)
        ingestion = IngestionService(
            source_dir=source_dir,
            vector_store=store,
            embeddings=embeddings,
            manifest_path=root / "rag_manifest.json",
        )
        return ingestion, retrieval

    def test_ingest_search_and_delete_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            ingestion, retrieval = self._build_stack(root)
            doc_path = root / "knowledge" / "hr" / "leave-policy.md"
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text(
                "年假管理制度：累计工作满 1 年享受 5 天年假，满 10 年享受 10 天。",
                encoding="utf-8",
            )
            record = asyncio.run(ingestion.index_file(doc_path, department="hr"))
            self.assertEqual(record.chunk_count, 1)
            self.assertEqual(record.source, "hr/leave-policy.md")

            result = asyncio.run(retrieval.search("公司年假有几天", department="hr"))
            self.assertTrue(result.hits)
            self.assertEqual(result.hits[0].chunk.source, "hr/leave-policy.md")

            docs = ingestion.list_documents()
            self.assertEqual(len(docs), 1)
            self.assertTrue(ingestion.delete(record.document_id))
            self.assertEqual(len(ingestion.list_documents()), 0)

    def test_rag_tool_workspace(self) -> None:
        from orchestra.tools import RetrievalRAGTool
        from orchestra.workspace.memory import MemoryWorkspace

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            ingestion, retrieval = self._build_stack(root)
            doc_path = root / "knowledge" / "hr" / "leave-policy.md"
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text("年假管理制度：累计满 1 年享受 5 天年假。", encoding="utf-8")
            asyncio.run(ingestion.index_file(doc_path, department="hr"))

            class DummyContext:
                def __init__(self):
                    self.workspace = MemoryWorkspace("rag-test")
                    self.context = {"department": "hr"}

            tool = RetrievalRAGTool(retrieval)
            context = DummyContext()
            result = asyncio.run(tool.run({"query": "年假有几天"}, context))
            self.assertTrue(result.success)
            self.assertIn("hr/leave-policy.md", result.metadata["sources"])
            files = asyncio.run(context.workspace.list_files())
            self.assertIn("rag/hr/leave-policy.md", files)


if __name__ == "__main__":
    unittest.main()
