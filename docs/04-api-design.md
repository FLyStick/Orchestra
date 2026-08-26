# API 与接口设计（P1）

## 1. 设计原则

- REST 为对外主协议，SSE 提供实时事件
- 任务提交采用异步模型：创建返回 task_id，结果通过查询或事件获取
- 错误响应统一结构，便于上层调用方处理
- 核心契约先以 Python dataclass 固定，JSON 为传输格式

## 2. REST 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /api/v1/tasks | 提交任务 |
| GET | /api/v1/tasks/{task_id} | 查询任务状态与结果 |
| DELETE | /api/v1/tasks/{task_id} | 取消任务 |
| GET | /api/v1/tasks/{task_id}/events | SSE 订阅任务事件 |
| GET | /healthz | 健康检查 |
| GET | /api/v1/scenarios | 获取已配置业务场景 |
| GET | /api/v1/sessions/{session_id}/workspace | 列出会话工作区文件与内容 |
| GET | /api/v1/sessions/{session_id}/workspace/files/{path} | 读取工作区单个文件 |
| POST | /api/v2/documents | 上传并索引单篇知识文档 |
| GET | /api/v2/documents | 查询已索引文档（可按部门过滤） |
| POST | /api/v2/documents/ingest | 扫描 data/knowledge 目录并索引 |
| DELETE | /api/v2/documents/{document_id} | 删除文档与向量 |
| POST | /api/v2/knowledge/search | 执行混合检索 |

## 3. TaskInput 示例

```json
{
  "query": "公司年假制度是什么？休半天怎么申请？",
  "session_id": "session-001",
  "user_id": "user-001",
  "context": {
    "department": "hr",
    "role": "employee"
  },
  "strategy": null,
  "budget": {
    "total_tokens": 100000,
    "per_agent_tokens": 20000,
    "allow_model_fallback": true
  },
  "max_iterations": 10,
  "workspace_enabled": true,
  "metadata": {
    "source": "web"
  }
}
```

## 4. RoutingDecision 示例

```json
{
  "strategy": "dag",
  "complexity_score": 0.62,
  "reason": "多步骤查询，需要制度检索与流程判断",
  "budget": {
    "total_tokens": 100000,
    "per_agent_tokens": 20000
  },
  "subtasks": [
    {
      "id": "t1",
      "goal": "检索年假制度",
      "dependencies": [],
      "tools": ["rag_search"],
      "agent_role": "generalist"
    }
  ]
}
```

## 5. TaskOutput 示例

```json
{
  "task_id": "task-0001",
  "status": "succeeded",
  "result": "公司年假制度...",
  "error": null,
  "token_usage": {
    "input_tokens": 3200,
    "output_tokens": 860
  },
  "duration_ms": 2800,
  "created_at": "2026-08-18T10:00:00+08:00",
  "updated_at": "2026-08-18T10:00:03+08:00"
}
```

## 6. SSE 事件示例

```text
event: task.routed
data: {"event_type": "task.routed", "task_id": "task-0001", "payload": {"strategy": "dag"}}

event: task.completed
data: {"event_type": "task.completed", "task_id": "task-0001", "payload": {"status": "succeeded"}}
```

## 7. 事件类型

- task.created
- task.routed
- strategy.started
- routing.escalated
- agent.started
- agent.completed
- workspace.updated
- token.updated
- budget.fallback
- tool.called
- tool.completed
- task.completed
- task.failed
- task.cancelled

## 8. 错误模型

```json
{
  "code": "INVALID_INPUT",
  "message": "query is required",
  "task_id": null
}
```

- 400 INVALID_INPUT：参数不合法
- 404 NOT_FOUND：任务不存在
- 409 CONFLICT：状态冲突，如取消已完成任务
- 429 BUDGET_EXCEEDED：预算或限流
- 500 INTERNAL_ERROR：内部异常

## 9. 已固化的 Python 契约

位于 `src/orchestra/contracts/`：

- task.py：TaskInput、TaskOutput、TaskStatus、TokenBudget
- routing.py：RoutingDecision、SubtaskSpec
- strategies.py：StrategyType、StrategyContext、StrategyResult、BaseStrategy
- workspace.py：WorkspaceConfig、Workspace Protocol
- events.py：EventType、TaskEvent

P2/P4.5 已按该契约实现 FastAPI 路由；P3 增加 React 工具循环、RAG/Workspace 工具与 Token 预算降级事件；包 1 增加 routing.escalated 与可解释路由结果；包 2 增加 /api/v2/* RAG 接口；包 3 接入 WorkflowDriver 后任务接口语义保持不变。
P4 已将 /api/v1/scenarios 改为读取 scenarios.py 的场景配置，返回 strategy/tools/subtasks 明细；DAG 子任务支持声明 tools 并在执行时发出 agent.started/agent.completed 与 tool.called/tool.completed 事件。

## 10. DAG + React 组合契约

SubtaskSpec 已增加 strategy 字段：direct | react | dag。RoutingDecision 顶层 strategy 仍可为 dag，DAG 节点通过 SubtaskSpec.strategy 表达 React 节点；API 场景清单会随 subtask 返回节点 strategy。

P4.5 已落地：React 节点复用同一工具循环，事件携带 subtask_id / agent_role，Token 预算全局共享。

## 11. RAG API v2

包 2 起新增 RAG 管理接口，前缀统一为 `/api/v2`：

| 接口 | 说明 |
| --- | --- |
| `POST /api/v2/documents` | `multipart/form-data`：file + department（+ title），上传后直接解析入库 |
| `GET /api/v2/documents?department=` | 返回文档清单与索引状态 |
| `POST /api/v2/documents/ingest` | 扫描 `data/knowledge` 目录增量索引 |
| `DELETE /api/v2/documents/{document_id}` | 删除文档对应的向量与 Manifest 记录 |
| `POST /api/v2/knowledge/search` | 查询 + department/top_k/mode，返回 hits/latency_ms/reranked/confidence |

检索请求示例：

```json
{
  "query": "公司年假有几天",
  "department": "hr",
  "top_k": 5,
  "mode": "hybrid"
}
```

响应包含命中的知识块、来源与相似度，供前端展示与 Agent 工具继续加工。
