# Orchestra 总体架构设计（已实现）

> 状态：P1-P4.5 与第二阶段包 1/2/3 已实现（2026-08-25）

## 1. 设计目标

- 统一接收公司内部 Agent 任务，避免各场景各自实现
- 按任务复杂度自动选择执行策略
- 保证任务可持久化、可重试、可恢复
- 支持多智能体共享上下文，控制 LLM 成本
- 全部执行过程可观测、可排查

## 2. 非目标

- 一期不实现复杂沙箱与多租户权限
- 一期不实现全部科学工作流
- 一期不依赖外部 Agent 平台

## 3. 分层架构

```text
接入层
  REST API | SSE 事件 | 飞书机器人（可选）
        |
编排层
  Orchestrator Router
  任务拆解 | 复杂度评分 | 策略选择 | 预算中间件
        |
策略层
  Simple | DAG | React | Swarm
  组合模式: Parallel / Sequential / Hybrid
        |
执行层
  LLM 调用 | RAG 检索 | 工具调用 | 飞书 API | 数据库
        |
基础设施
  SQLite | Redis Streams | ChromaDB | 事件流与监控
```

## 4. 核心组件职责

| 组件 | 职责 |
| --- | --- |
| API Service | 接收 TaskInput，创建任务，返回任务状态与 SSE 事件 |
| Orchestrator Router | 任务拆解、复杂度评分、策略选择、预算分配 |
| Strategy 实现 | 按策略执行任务，组合模式与工具调用 |
| Workspace | 管理会话级共享文件与中间结果 |
| Token Budget | 限额、降级、用量记录 |
| RAG Service | 文档解析、Embedding、ChromaDB、混合检索与 Rerank |
| Workflow Driver | SQLite/Redis 双驱动：状态迁移、重试、恢复、事件总线 |
| Event Stream | 发布任务事件，支持订阅与时间线重建 |
| 存储 | 任务、事件、token_usage 持久化 |

## 5. 核心执行流程

1. 客户端提交 TaskInput，API 创建任务并返回 task_id
2. Router 加载任务，执行拆解与复杂度评分
3. Router 输出 RoutingDecision，包含策略、预算与子任务
4. Strategy 在 WorkflowDriver 中执行：默认 SQLite 进程内调度，Redis 模式由 Worker 消费命令流分发
5. 执行过程中写入 Workspace、更新 Token 用量、发布事件
6. 完成或失败后更新任务状态，SSE 输出最终结果

## 6. 数据模型

### tasks

- task_id、query、session_id、user_id
- strategy、status、result、error
- token_usage、created_at、updated_at

### task_events

- event_id、task_id、event_type、payload、occurred_at

### token_usage

- task_id、agent_id、input_tokens、output_tokens、model、estimated_cost

### workspace_files

- session_id、path、content_type、updated_at

## 7. 任务状态机

```text
PENDING -> ROUTING -> RUNNING -> SUCCEEDED
              |           |
              |           +----> RETRYING -> RUNNING
              |           +----> FAILED
              +----> CANCELLED
```

复杂任务在等待依赖时进入 WAITING_DEPENDENCY。状态迁移由 WorkflowDriver 原子认领（expected_statuses + attempt_count），包 3 已实现 SQLite/Redis 双驱动、指数退避重试与启动恢复。

## 8. 可观测性

- 每个状态迁移发出 TaskEvent
- SSE 实时推送事件
- 任务时间线由事件重建
- 预留指标点：任务成功率、失败率、平均耗时、平均成本、并发执行数

## 9. 部署视图（开发环境）

- FastAPI + WorkflowDriver：默认 SQLite 进程内执行，Redis 模式下启动 Worker 消费命令流
- ChromaDB 保存知识向量（本地持久化或 Docker Server）
- Redis 用于命令流 / 事件流与延迟重试（可选，未部署时自动回退）
- SQLite 保存任务、事件、Token 用量与重试投影
- LLM 通过统一 Provider 访问
- 可选飞书接入作为业务验证入口

## 10. 策略正交化设计

当前 StrategyType 将 Simple / DAG / React 视为互斥策略，Executor 单次只选一个。P4 评审后确定下一阶段按两个维度建模：

- DAG：执行拓扑，负责依赖、调度、Workspace、预算与汇总。
- React：单节点推理模式，负责工具调用循环。
- 单个 DAG 节点可配置 direct / react / dag 三种模式，递归深度限制 2 层。

详细设计见 docs/07-dag-react-composition.md。P4.5 已落地：direct/react/dag 三种节点模式、共享 Token 预算与 subtask_id 事件归属均已实现。

## 11. 第二阶段落地（包 1-3 已实现）

- 包 1：ScorerV2、RuleRouter、DecompositionPlanner/PlanValidator 与路由/拆解评测底座，路由黄金用例 89/89。
- 包 2：真实 RAG 落地，覆盖文档解析、分块、Embedding、ChromaDB、BM25+向量 RRF 与 Rerank，15 份演示文档入库、真实混合检索已跑通。
- 包 3：Redis Streams + 自研状态机工作流引擎，实现 XACK/XAUTOCLAIM、ZSET 延迟重试、崩溃恢复与多 Worker 消费，全量测试 62 项通过。

详细设计见 [docs/08-package1-technical-design.md](docs/08-package1-technical-design.md)、[docs/09-package2-technical-design.md](docs/09-package2-technical-design.md) 与 [docs/11-package3-technical-design.md](docs/11-package3-technical-design.md)。
