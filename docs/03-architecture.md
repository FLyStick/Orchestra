# Orchestra 总体架构设计（P1）

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
  Temporal | Redis | PostgreSQL | 事件流与监控
```

## 4. 核心组件职责

| 组件 | 职责 |
| --- | --- |
| API Service | 接收 TaskInput，创建任务，返回任务状态与 SSE 事件 |
| Orchestrator Router | 任务拆解、复杂度评分、策略选择、预算分配 |
| Strategy 实现 | 按策略执行任务，组合模式与工具调用 |
| Workspace | 管理会话级共享文件与中间结果 |
| Token Budget | 限额、降级、用量记录 |
| Event Stream | 发布任务事件，支持订阅与时间线重建 |
| 存储 | 任务、事件、token_usage 持久化 |

## 5. 核心执行流程

1. 客户端提交 TaskInput，API 创建任务并返回 task_id
2. Router 加载任务，执行拆解与复杂度评分
3. Router 输出 RoutingDecision，包含策略、预算与子任务
4. Strategy 在 Temporal Workflow 中启动，副作用走 Activity
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

复杂任务在等待依赖时进入 WAITING_DEPENDENCY。

## 8. 可观测性

- 每个状态迁移发出 TaskEvent
- SSE 实时推送事件
- 任务时间线由事件重建
- 预留指标点：任务成功率、失败率、平均耗时、平均成本、并发执行数

## 9. 部署视图（开发环境）

- FastAPI Service + Temporal Worker 运行在同一进程或独立进程
- Redis 用于事件流、缓存与 Workspace（可选）
- PostgreSQL/MySQL 保存任务与用量数据
- LLM 通过统一 Provider 访问
- 可选飞书接入作为业务验证入口

## 10. 策略正交化设计

当前 StrategyType 将 Simple / DAG / React 视为互斥策略，Executor 单次只选一个。P4 评审后确定下一阶段按两个维度建模：

- DAG：执行拓扑，负责依赖、调度、Workspace、预算与汇总。
- React：单节点推理模式，负责工具调用循环。
- 单个 DAG 节点可配置 direct / react / dag 三种模式，递归深度限制 2 层。

详细设计见 docs/07-dag-react-composition.md。P4.5 已落地：direct/react/dag 三种节点模式、共享 Token 预算与 subtask_id 事件归属均已实现。
