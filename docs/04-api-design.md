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

P2/P3 已按该契约实现 FastAPI 路由；P3 增加 React 工具循环、RAG/Workspace 工具与 Token 预算降级事件。