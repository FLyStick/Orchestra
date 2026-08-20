"""包 2 RAG 能力：Embedding、Rerank、Chroma、Ingestion 与混合检索。"""
from .embeddings import EmbeddingProvider, OpenAICompatEmbeddingProvider
from .ingestion import IngestionService
from .retrieval import RetrievalService
from .service import create_rag_stack
from .vector_store import ChromaVectorStore

__all__ = [
    "ChromaVectorStore",
    "EmbeddingProvider",
    "IngestionService",
    "OpenAICompatEmbeddingProvider",
    "RetrievalService",
    "create_rag_stack",
]
