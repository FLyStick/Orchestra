# Orchestra 开发环境文档

## 1. 环境要求

- Python 3.11+
- Conda 环境：`orchestra`（已创建：`C:\Users\20235\.conda\envs\orchestra`）
- 默认使用 Mock LLM，无需 API Key 即可本地运行

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

默认使用 Mock LLM：

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

## 6. 运行测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

安装依赖后，全部测试应通过，包括 API 集成测试。未安装 FastAPI/httpx 时，API 测试会自动跳过。

## 7. 目录说明

- `src/orchestra/api.py`：FastAPI 入口与 REST/SSE 接口
- `src/orchestra/executor.py`：任务执行器，负责路由、执行、Token 记录
- `src/orchestra/router.py`：规则路由与复杂度评分
- `src/orchestra/strategies/`：Simple 与 DAG 策略
- `src/orchestra/store.py`：SQLite 任务、事件与 Token 持久化
- `src/orchestra/llm.py`：Mock 与 OpenAI 兼容 Provider
- `src/orchestra/workspace/`：本地文件与内存 Workspace

## 8. 常见问题

- 如果 `python -m orchestra.main` 报模块找不到，先确认当前目录是项目根目录并激活 orchestra 环境。
- 如果 API 测试被跳过，执行 `pip install -r requirements-dev.txt` 与 `pip install -e .` 后重跑。
- 如果使用 OpenAI 兼容服务，确认地址以 `/v1` 结尾并设置 API Key。