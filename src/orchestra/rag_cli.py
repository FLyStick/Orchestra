"""包 2 RAG 命令行：索引演示/真实文档、检索与文档管理。

用法示例：
    python -m orchestra.rag_cli seed
    python -m orchestra.rag_cli ingest --department hr
    python -m orchestra.rag_cli search --query "年假有几天" --department hr
    python -m orchestra.rag_cli list --department hr
    python -m orchestra.rag_cli delete --document-id <id>

命令说明：
    seed    写入并索引内置演示知识库（KNOWLEDGE_DOCS）
    ingest  扫描 data/knowledge 目录下的真实文档并索引
    search  混合检索（向量 + BM25 融合，可选 Rerank）
    list    读取 rag_manifest.json 列出已索引文档
    delete  按 document_id 删除文档及其向量
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .config import get_settings
from .rag.service import create_rag_stack


def _require_stack(settings: Any):
    """获取 RAG 组件栈，未启用时给出可操作的错误提示。

    通过 create_rag_stack 工厂一次性构建检索（RetrievalService）与
    索引（IngestionService）两条链路；任一前置条件缺失（如
    ORCHESTRA_RAG_ENABLED 未开启、未配置 Embedding/ChromaDB）时
    返回 (None, None)，此处直接报错退出，避免后续空指针。
    """
    retrieval, ingestion = create_rag_stack(settings)
    if retrieval is None or ingestion is None:
        print(
            "RAG 未启用：请检查 .env 中 ORCHESTRA_RAG_ENABLED=true，"
            "并配置 ORCHESTRA_EMBEDDING_API_KEY / ChromaDB 地址。",
            file=sys.stderr,
        )
        sys.exit(1)
    return retrieval, ingestion


async def _seed(ingestion, department: str | None) -> dict[str, Any]:
    """seed 子命令：写入内置演示文档并建立索引。

    seed_demo 返回 (records, errors)：records 为逐文档索引结果，
    errors 为失败明细；统一转成 dict 供 JSON 输出。
    """
    records, errors = await ingestion.seed_demo(department)
    return {"records": [record.to_dict() for record in records], "errors": errors}


async def _ingest(ingestion, department: str | None) -> dict[str, Any]:
    """ingest 子命令：扫描知识目录下的真实文档并索引。

    与 seed 的区别：不写内置文档，只对 data/knowledge 下已有的
    Markdown 文件做增量索引（按 sha256 版本指纹去重）。
    """
    records, errors = await ingestion.index_directory(department)
    return {"records": [record.to_dict() for record in records], "errors": errors}


async def _search(retrieval, args) -> dict[str, Any]:
    """search 子命令：执行混合检索并返回命中文档。

    检索模式由 --mode 指定（hybrid/vector/keyword），默认走配置；
    top_k 控制返回条数，department 限定检索的 Collection 分桶。
    """
    result = await retrieval.search(
        args.query,
        department=args.department,
        top_k=args.top_k,
        mode=args.mode,
    )
    return result.to_dict()


def _list_documents(ingestion, department: str | None) -> dict[str, Any]:
    """list 子命令：从 rag_manifest.json 读取文档清单。"""
    return {"documents": ingestion.list_documents(department)}


def _delete(ingestion, document_id: str) -> dict[str, Any]:
    """delete 子命令：按 document_id 删除文档（含向量与清单记录）。

    删除不存在的文档视为错误，打印提示并以非零码退出。
    """
    if not ingestion.delete(document_id):
        print(f"文档不存在：{document_id}", file=sys.stderr)
        sys.exit(1)
    return {"document_id": document_id, "status": "deleted"}


def main() -> None:
    """RAG 命令入口：解析子命令并分发到对应处理函数。"""
    parser = argparse.ArgumentParser(description="Orchestra 包 2 RAG 工具")
    # 子命令必须显式指定（required=True），未指定时 argparse 直接报错
    subparsers = parser.add_subparsers(dest="command", required=True)

    # seed：写入并索引内置演示知识库
    seed_parser = subparsers.add_parser("seed", help="写入并索引内置演示知识库")
    seed_parser.add_argument("--department", default=None)

    # ingest：扫描知识目录并索引
    ingest_parser = subparsers.add_parser("ingest", help="扫描知识目录并索引")
    ingest_parser.add_argument("--department", default=None)

    # search：混合检索测试
    search_parser = subparsers.add_parser("search", help="混合检索测试")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--department", default=None)
    search_parser.add_argument("--top-k", type=int, default=5)
    search_parser.add_argument("--mode", choices=("hybrid", "vector", "keyword"), default=None)

    # list：列出已索引文档
    list_parser = subparsers.add_parser("list", help="列出已索引文档")
    list_parser.add_argument("--department", default=None)

    # delete：删除单个文档
    delete_parser = subparsers.add_parser("delete", help="删除单个文档")
    delete_parser.add_argument("--document-id", required=True)

    args = parser.parse_args()
    # 读取配置并构建 RAG 组件栈（未启用则在此退出）
    settings = get_settings()
    retrieval, ingestion = _require_stack(settings)

    # 按子命令分发：seed/ingest/search 为异步操作，用 asyncio.run 包装；
    # list/delete 为同步操作，直接调用
    if args.command == "seed":
        payload = asyncio.run(_seed(ingestion, args.department))
    elif args.command == "ingest":
        payload = asyncio.run(_ingest(ingestion, args.department))
    elif args.command == "search":
        payload = asyncio.run(_search(retrieval, args))
    elif args.command == "list":
        payload = _list_documents(ingestion, args.department)
    else:
        payload = _delete(ingestion, args.document_id)

    # 统一以 JSON 输出（ensure_ascii=False 保留中文，indent=2 便于阅读）
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
