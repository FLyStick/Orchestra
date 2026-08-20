"""RAG 组件工厂：按配置创建 RetrievalService 与 IngestionService。

返回 None 表示 RAG 未启用（例如未配置 API Key），框架保留旧 KeywordsRAGTool
作为 Mock/离线兜底，不影响包 1 与 P4 演示链路。
"""
from __future__ import annotations

from typing import Any

from .embeddings import create_embedding_provider
from .ingestion import IngestionService
from .rerank import RerankProvider
from .retrieval import RetrievalService
from .vector_store import ChromaVectorStore


def create_rag_stack(settings: Any) -> tuple[RetrievalService | None, IngestionService | None]:
    """创建 RAG 组件栈；任一前置条件缺失时返回 (None, None)。"""
    if not getattr(settings, "rag_enabled", False):
        return None, None
    embeddings = create_embedding_provider(settings)
    if embeddings is None:
        return None, None
    try:
        store = ChromaVectorStore(
            path=settings.chroma_path,
            host=settings.chroma_host or None,
            port=settings.chroma_port,
            collection_prefix=settings.collection_prefix,
        )
    except (ImportError, RuntimeError, ValueError):
        return None, None

    reranker: RerankProvider | None = None
    if settings.rerank_enabled and settings.rerank_api_key and settings.rerank_base_url:
        try:
            reranker = RerankProvider(
                api_key=settings.rerank_api_key,
                base_url=settings.rerank_base_url,
                model=settings.rerank_model,
                top_n=settings.rerank_top_n,
            )
        except ValueError:
            reranker = None

    retrieval = RetrievalService(
        store=store,
        embeddings=embeddings,
        reranker=reranker,
        top_k=settings.retrieval_top_k,
        mode=settings.retrieval_mode,
        rerank_top_n=settings.rerank_top_n,
        min_score=settings.retrieval_min_score,
    )
    ingestion = IngestionService(
        source_dir=settings.knowledge_source_dir,
        vector_store=store,
        embeddings=embeddings,
        manifest_path=settings.rag_manifest_file,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
    )
    return retrieval, ingestion
