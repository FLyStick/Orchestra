# 包 2 实施报告：真实 RAG 落地

> 日期：2026-08-20
> 状态：已实现并完成真实链路验证；hit@5 / MRR 占位指标待黄金检索用例回填
> 前置条件：Conda 环境 `orchestra`、Docker ChromaDB（`docker/docker-compose.yml`）、`.env` 已配置 Embedding / Rerank

## 1. 交付范围

- 文档导入与解析：Markdown / TXT / PDF / Word / Excel / PPT。
- 分块与索引：递归分块、重叠、文档版本指纹、`data/rag_manifest.json` 索引清单。
- 存储：ChromaDB 本地持久化或 Server 模式，按部门 Collection 隔离（`orchestra_hr` 等）。
- 检索：BM25 + 向量 RRF 融合，支持 `hybrid` / `vector` / `keyword` 三种模式，可选 Rerank 精排。
- 接入：CLI、FastAPI `/api/v2/*` 接口、`RetrievalRAGTool`（Executor/RAG 工具栈）。
- 降级：RAG 未启用或服务缺失时保留原 `KeywordRAGTool`，不影响 P4 / P4.5 演示链路。

## 2. 模块清单

| 模块 | 职责 |
| --- | --- |
| `src/orchestra/contracts/rag.py` | 知识块、文档记录、检索结果契约 |
| `src/orchestra/rag/parsing.py` | 多格式文档解析与文本清洗 |
| `src/orchestra/rag/chunking.py` | 递归分隔符分块与重叠 |
| `src/orchestra/rag/embeddings.py` | OpenAI 兼容 / 本地 Embedding Provider |
| `src/orchestra/rag/rerank.py` | MaaS text-rerank 调用 |
| `src/orchestra/rag/vector_store.py` | ChromaDB 本地 / Server 封装 |
| `src/orchestra/rag/retrieval.py` | BM25 + 向量 RRF、Rerank 精排 |
| `src/orchestra/rag/ingestion.py` | 文档导入、向量化、Manifest 更新 |
| `src/orchestra/rag/service.py` | RAG 组件工厂 |
| `src/orchestra/rag_cli.py` | seed / ingest / search / list / delete CLI |

## 3. 配置

非敏感变量写入 `.env` / `.env.example`：

- `ORCHESTRA_RAG_ENABLED=true`
- `ORCHESTRA_EMBEDDING_PROVIDER=openai`
- `ORCHESTRA_EMBEDDING_MODEL=qwen3.7-text-embedding`
- `ORCHESTRA_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`
- `ORCHESTRA_CHROMA_HOST=127.0.0.1`、`ORCHESTRA_CHROMA_PORT=8001`
- `ORCHESTRA_RERANK_ENABLED=true`、`ORCHESTRA_RERANK_MODEL=gte-rerank-v2`
- `ORCHESTRA_RETRIEVAL_MODE=hybrid`、`ORCHESTRA_RETRIEVAL_TOP_K=5`

API Key 只放 `.env`，`.gitignore` 已忽略，README / 实施文档不记录密钥。

## 4. 使用方式

```powershell
$env:PYTHONPATH = "src"
python -m orchestra.rag_cli seed
python -m orchestra.rag_cli search --query "公司年假有几天" --department hr --top-k 5 --mode hybrid
python -m orchestra.rag_cli list --department hr
python -m orchestra.rag_cli delete --document-id <document_id>
```

上传单个知识文档：

```powershell
curl -X POST http://127.0.0.1:8000/api/v2/documents `
  -F "file=@data/knowledge/hr/leave-policy.md" `
  -F "department=hr"
```

## 5. 真实链路验证

验证环境：

- ChromaDB：Docker `127.0.0.1:8001`，心跳接口返回 200。
- Embedding：DashScope OpenAI 兼容接口 `qwen3.7-text-embedding`。
- Rerank：MaaS `gte-rerank-v2`。

验证结果：

- `python -m orchestra.rag_cli seed`：hr / risk / finance 共 15 份演示文档全部入库，`errors=[]`。
- `python -m orchestra.rag_cli search --query "公司年假有几天" --department hr --top-k 5 --mode hybrid`：Top1 命中 `hr/leave-policy.md`，`reranked=true`，单次约 736ms。
- 全量单元/API 集成测试 55 项通过（含包 2 分块/索引/检索/工具与 RAG API 降级用例）。

## 6. 已知边界与后续

- 检索评测指标（hit@5 / MRR / 溯源率）仍是占位，需要为 hr / risk / finance 沉淀黄金检索用例后回填。
- ChromaDB 容器重启后的持久化复用需按验收标准复测一次。
- Rerank 失败不阻断检索，自动降级为 RRF 融合结果。
- 本地 `sentence-transformers` 为可选路径，未安装时跳过 `local` 模式。
