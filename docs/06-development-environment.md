# Orchestra 开发环境文档

## 1. 环境要求

- Python 3.11+
- Conda 环境：`orchestra`（已创建：`C:\Users\20235\.conda\envs\orchestra`）
- 默认使用 Mock LLM，无需 API Key 即可本地运行
- Redis / ChromaDB 的 Docker 编排位于 `docker/`，手动部署见 [../docker/README.md](../docker/README.md)；包 2 需要 ChromaDB（Docker Server 或本地模式），包 3 需要 Redis

## 2. 激活环境并安装依赖

```powershell
conda activate orchestra
cd D:\实习记录\组内项目\Orchestra
pip install -r requirements.txt
```

开发与测试额外安装：

```powershell
pip install -r requirements-dev.txt
pip install -e .
```

## 3. 启动服务

首次运行前先复制 `.env.example` 为 `.env`：

```powershell
Copy-Item .env.example .env
```

默认无 API Key 时使用 Mock LLM：

```powershell
python -m orchestra.main
```

启动后访问：

- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/healthz

## 4. 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| ORCHESTRA_LLM_PROVIDER | mock | mock 或 openai |
| OPENAI_API_KEY | 空 | 使用 openai 时必填 |
| OPENAI_BASE_URL | https://api.openai.com/v1 | OpenAI 兼容地址 |
| ORCHESTRA_LLM_MODEL | gpt-4o-mini | 默认模型 |
| ORCHESTRA_FALLBACK_MODEL | 空 | 降级模型，可选 |
| ORCHESTRA_DB_PATH | data/orchestra.db | SQLite 路径 |
| ORCHESTRA_WORKSPACE_ROOT | data/workspaces | Workspace 根目录 |
| ORCHESTRA_HOST | 127.0.0.1 | 监听地址 |
| ORCHESTRA_PORT | 8000 | 监听端口 |
| ORCHESTRA_RAG_ENABLED | false | 包 2 RAG 总开关 |
| ORCHESTRA_EMBEDDING_PROVIDER | openai | openai 兼容或 local |
| ORCHESTRA_EMBEDDING_MODEL | qwen3.7-text-embedding | Embedding 模型 |
| ORCHESTRA_EMBEDDING_BASE_URL | https://dashscope.aliyuncs.com/compatible-mode/v1 | Embedding API 地址 |
| ORCHESTRA_EMBEDDING_API_KEY | 空 | Embedding API Key |
| ORCHESTRA_CHROMA_PATH | data/chroma | 本地持久化目录 |
| ORCHESTRA_CHROMA_HOST | 127.0.0.1 | ChromaDB Server 地址；本地模式留空 |
| ORCHESTRA_CHROMA_PORT | 8001 | ChromaDB 端口 |
| ORCHESTRA_RERANK_ENABLED | false | 是否启用 Rerank |
| ORCHESTRA_RERANK_MODEL | gte-rerank-v2 | Rerank 模型 |
| ORCHESTRA_RERANK_BASE_URL | 空 | MaaS Rerank 服务地址 |
| ORCHESTRA_RERANK_API_KEY | 空 | Rerank API Key |
| ORCHESTRA_RETRIEVAL_MODE | hybrid | hybrid/vector/keyword |
| ORCHESTRA_RETRIEVAL_TOP_K | 5 | 返回命中条数 |

Windows PowerShell 示例：

```powershell
$env:ORCHESTRA_LLM_PROVIDER = "openai"
$env:OPENAI_API_KEY = "sk-..."
$env:ORCHESTRA_LLM_MODEL = "gpt-4o-mini"
python -m orchestra.main
```

## 5. 快速验证

提交一个简单任务：

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/tasks `
  -H "Content-Type: application/json" `
  -d '{\"query\": \"报销标准是什么\", \"session_id\": \"demo-1\"}'
```

查询任务结果：

```powershell
curl http://127.0.0.1:8000/api/v1/tasks/<task_id>
```

订阅事件流：

```powershell
curl -N http://127.0.0.1:8000/api/v1/tasks/<task_id>/events
```

提交 React 任务（Mock 下会自动模拟一次 `rag_search` 工具调用）：

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/tasks `
  -H "Content-Type: application/json" `
  -d '{\"query\": \"请调用rag_search查询报销标准\", \"session_id\": \"demo-react\"}'
```

查看会话工作区：

```powershell
curl http://127.0.0.1:8000/api/v1/sessions/demo-react/workspace
```


提交 P4 人事制度问答（自动走 React + RAG）：

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/tasks `
  -H "Content-Type: application/json" `
  -d '{\"query\": \"公司年假制度怎么规定\", \"session_id\": \"demo-hr\", \"context\": {\"department\": \"hr\"}}'
```

提交 P4 风控条款审查（自动走 DAG 三阶段，包含合同提取与 RAG 工具调用）：

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/tasks `
  -H "Content-Type: application/json" `
  -d '{\"query\": \"分析合同付款风险然后生成合规清单\", \"session_id\": \"demo-risk\"}'
```

### 5.1 包 2 RAG 快速验证（需 ChromaDB 与 API Key）

```powershell
$env:PYTHONPATH = "src"
python -m orchestra.rag_cli seed
python -m orchestra.rag_cli search --query "公司年假有几天" --department hr --top-k 5 --mode hybrid
python -m orchestra.rag_cli list --department hr
```

## 6. 运行测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

P4 黄金用例评测（Mock 模式不消耗真实 Token）：

```powershell
python -m orchestra.evals --provider mock
python -m orchestra.evals --provider openai --output data/eval-report.json
```

安装依赖后，全部测试应通过，包括 API 集成测试。未安装 FastAPI/httpx 时，API 测试会自动跳过。

## 7. 目录说明

- `src/orchestra/api.py`：FastAPI 入口与 REST/SSE 接口
- `src/orchestra/executor.py`：任务执行器，负责路由、执行、Token 记录
- `src/orchestra/router.py`：规则路由、复杂度评分与 React 路由
- `src/orchestra/budget.py`：Token 总预算、动态上限与模型降级
- `src/orchestra/tools.py`：RAG 检索、合同提取与 Workspace 工具注册
- `src/orchestra/knowledge.py`：P4 演示制度文档与演示合同
- `src/orchestra/scenarios.py`：P4 业务场景与 DAG 子任务配置
- `src/orchestra/evals.py`：30 条 HR 黄金用例评测器
- `src/orchestra/strategies/`：Simple、DAG 与 React 策略
- `src/orchestra/store.py`：SQLite 任务、事件与 Token 持久化
- `src/orchestra/llm.py`：Mock 与 OpenAI 兼容 Provider
- `src/orchestra/workspace/`：本地文件与内存 Workspace

## 8. 常见问题

- 如果 `python -m orchestra.main` 报模块找不到，先确认当前目录是项目根目录并激活 orchestra 环境。
- 如果 API 测试被跳过，执行 `pip install -r requirements-dev.txt` 与 `pip install -e .` 后重跑。
- 如果使用 OpenAI 兼容服务，确认地址以 `/v1` 结尾并设置 API Key。