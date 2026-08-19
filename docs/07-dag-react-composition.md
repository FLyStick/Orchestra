# DAG + React 组合编排设计

> 状态：已实施；DAG 与 React 从互斥策略升级为“执行拓扑 + 节点行为”的正交组合。本文记录目标形态、改造方案与验收口径。

## 1. 背景

当前 `StrategyType` 将 Simple / DAG / React / Swarm 视为互斥策略，Executor 单次只选择一个策略。P4 落地后的判断是：

- 风控条款审查：DAG 适合作为固定三阶段流程，但「规则匹配」节点需要根据 t1 识别出的条款反复检索，固定调用一次 `rag_search` 偏弱。
- 人事制度问答：全量走 React 对高频单跳问答偏重，Simple + RAG 更适合大多数问题，复杂、跨制度或模糊问题再升级 React/DAG。

## 2. 核心设计

DAG 与 React 不是同一层概念：

- DAG = 执行拓扑：依赖、调度、并行、Workspace、预算、汇总。
- React = 节点行为：推理 → 工具调用 → 观察 → 再推理。

因此将策略拆成两个维度：DAG 负责把子任务串成图，每个节点再声明自己的执行模式。

`SubtaskSpec` 已增加节点级策略字段：

```python
@dataclass(frozen=True)
class SubtaskSpec:
    id: str
    goal: str
    dependencies: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    strategy: str = "direct"  # direct | react | dag
    agent_role: str = "generalist"
    metadata: dict[str, Any] = field(default_factory=dict)
```

## 3. 目标场景配置

### 风控条款审查

| 节点 | 节点策略 | 工具 | 说明 |
| --- | --- | --- | --- |
| t1 条款识别 | direct | contract_context | 一次工具调用后生成条款摘要 |
| t2 规则匹配 | react | rag_search, workspace_read | 根据条款反复检索、修正关键词 |
| t3 审查清单 | direct | 无 | 依赖 t2 结果生成最终清单 |

### 人事制度问答

- 简单单跳问题：Simple + RAG。
- 模糊、跨制度比较、需要多次检索的问题：React + RAG。
- 多步骤并列任务：DAG + RAG。

## 4. 实现要点（已落地）

1. `SubtaskSpec` 增加 `strategy` 字段，默认 `direct`，保持现有行为不变。
2. 将 `ReactStrategy` 的核心逻辑抽成可复用的 `react_loop`，DAG 节点可以直接复用，而不是把整个策略当作子流程塞入。
3. `DAGStrategy` 在调度子任务时分派：`direct` 走单次 LLM + 声明工具，`react` 走节点级 ReAct 循环。
4. Token 预算提升为流程级：嵌套 React 节点需要共享 DAG 的累计用量，避免重复计算或互不可见。
5. 事件增加 `subtask_id` / `agent_role` 维度，SSE 能区分 DAG 内每个节点及其工具调用。
6. 支持可选的递归 DAG，但递归深度限制为 2 层，避免循环与上下文爆炸。
7. 后续可将场景配置升级为 YAML/Python 模板，节点显式声明 `strategy` 和 `tools`。

## 5. 验收标准

1. 风控任务仍路由到 DAG，事件中能区分 t1/t2/t3 与各自工具。
2. t2 在首次检索不足时，能主动再次调用 `rag_search` 或读取 Workspace。
3. 与 DAG + 内置 RAG 对比通过率、Token 成本和 P95 耗时（真实模型对比留待验收回填）。
4. HR 简单问题走 Simple + RAG，复杂问题可升级 React。
5. 30 条黄金用例评测与全部单元测试通过（Mock 评测 30/30，34 项单测通过）。

## 6. 参考

- Shannon 三层架构：Router → Strategy Workflows → Patterns Library。
- Shannon YAML 模板中，`type` 表示节点类型（simple/cognitive/dag/supervisor），`strategy` 表示节点推理模式（react/chain_of_thought/reflection 等）。
- 参考文档：docs/multi-agent-workflow-architecture.md、config/workflows/examples/market_analysis.yaml。

## 7. 实施清单（已完成）

- [x] 扩展 SubtaskSpec 节点策略字段
- [x] 抽取可复用 react_loop（run_node / ReactNodeResult）
- [x] DAGStrategy 节点策略分派
- [x] 共享 Token 预算与事件归属
- [x] 风控 t2 升级为 React，HR 默认 Simple+RAG
- [x] 补充 DAG+React 自动化测试；真实模型 A/B 对比评测待验收回填
- [x] 同步 README、API、开发文档
