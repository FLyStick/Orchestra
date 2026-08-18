# Shannon 架构调研

> 调研时间：2026-08-18
> 调研对象：Kocoro-lab/Shannon（本地 Shannon-main 源码与文档）
> 目标：提取可借鉴的多智能体编排思想，为 Orchestra P1 架构设计提供基线

## 1. 调研结论摘要

Shannon 是面向生产环境的开源多智能体框架，核心价值在于把「路由、策略、模式、执行」分层解耦，并依靠 Temporal 工作流引擎获得持久化与可恢复能力。

Orchestra 值得借鉴的点：

- Orchestrator Router 的任务拆解、复杂度评分与动态策略路由
- Strategy Workflow + Pattern 的组合式编排
- Temporal Workflow / Activity 边界划分
- Swarm 模式下 Lead Agent + 角色 Agent 的事件驱动协作
- 会话级 Workspace 共享上下文
- Token 预算控制与模型降级
- SSE 事件流和任务时间线可观测性

Orchestra 裁剪的点：

- 不实现 WASI 沙箱、EKS/Firecracker 微虚拟机等重部署能力
- 不照搬全部五类科学工作流，P1-P3 只落地 Simple、DAG、React；Swarm 作为后续扩展
- 不复制多租户权限体系，先服务公司内部场景

## 2. 总体架构

Shannon 采用三层编排结构：

```text
Orchestrator Router
  查询拆解 | 复杂度分析 | 策略选择 | 预算中间件
        |
Strategy Workflows
  DAG | React | Research | Exploratory | Scientific
        |
Patterns Library
  执行模式: Parallel / Sequential / Hybrid
  推理模式: React / Reflection / CoT / Debate / ToT
        |
Activities / Infrastructure
  Temporal | Redis | Postgres | LLM Service | Event Stream
```

## 3. 核心机制分析

### 3.1 路由机制

- 入口对所有任务进行查询拆解与复杂度评分
- 简单任务（评分低于 0.3）直接执行 Simple 策略
- 复杂任务按认知策略路由到 DAG、React、Research 等工作流
- 预算通过中间件统一约束，失败与超额有明确处理路径
- 高级版本用 epsilon-greedy 按历史成功率、延迟和 Token 效率持续优化路由

### 3.2 策略工作流与模式库

- DAG 负责通用任务拆解、依赖执行和并行/串行/混合调度
- React 负责 Reason-Act-Observe 工具循环
- 模式库支持组合，例如 Scientific 工作流由 CoT、Debate、ToT、Reflection 组合而成
- 对 Orchestra 而言，先抽象 BaseStrategy 接口，再逐步注册具体策略实现

### 3.3 Temporal 执行模型

- Workflow 只做确定性编排：规划、路由、分支、等待
- 外部副作用全部封装在 Activity：LLM 调用、Redis、数据库、事件输出
- Worker 消费统一队列，Registry 统一注册 Workflow 与 Activity
- 支持重试、信号、查询、Schedule 与时间旅行调试

### 3.4 Swarm 协作模型

- Lead Agent 负责拆解任务、生成初始计划
- 子 Agent 执行 reason-act 循环，支持工具调用与 Workspace 文件共享
- Lead 通过事件监听子 Agent 状态，可重新分配 idle 智能体
- Agent 完成时执行质量自检并产出关键发现

### 3.5 会话 Workspace

- 每个会话拥有隔离的工作区
- 文件操作按角色开放权限，降低越权风险
- 多智能体通过工作区文件交换中间结果，避免只依赖上下文传递
- 存储可抽象为文件系统或 Redis，接口保持一致

### 3.6 Token 预算与成本

- 预算开启与关闭走两条记录路径，保证 token 只记录一次
- 每个 Agent 可设置独立预算，超限触发模型降级或终止
- token_usage 持久化，支持成本统计与优化

### 3.7 可观测性

- 事件通过 Redis Streams 发布并落库
- 前端通过 SSE 订阅实时事件
- 配合任务时间线、Prometheus 指标与 OTel 链路追踪

## 4. 可借鉴与裁剪矩阵

| Shannon 能力 | Orchestra 决策 | 理由 |
| --- | --- | --- |
| Router 复杂度路由 | 保留，P2 先实现规则评分 | 统一入口与策略选择是核心卖点 |
| DAG / React 策略 | 保留，P2/P3 实现 | 覆盖多步骤与工具调用场景 |
| Research 等科学工作流 | 暂不落地 | 公司场景暂不需要 |
| Temporal | 保留，P2 接入 | 持久化与故障恢复可解释性强 |
| Swarm | 保留为后续扩展 | 多智能体协作，但实现成本高，P4 不依赖 |
| Workspace | 保留，P3 实现 | 支撑多智能体上下文共享 |
| Token 预算 | 保留，P3 实现 | 成本可控是重要成果 |
| WASI / VM 沙箱 | 不采纳 | 内部 MVP 不需要重沙箱 |
| 多租户 / OPA | 不采纳 | 先服务内部，后续按需补 |

## 5. 对 Orchestra P1 的输入

- 确定分层：接入层、编排层、策略层、执行层、基础设施层
- 确定核心契约：TaskInput、TaskStatus、RoutingDecision、StrategyContext、Workspace、TaskEvent
- 确定 P2 范围：先做 Simple + DAG，用规则路由，接入 Temporal 前保持可替换边界
- 确定 P3 范围：React、Workspace、Token 预算、SSE
- 确定 P4 范围：人事制度问答、风控条款审查 2 个业务原型与评测；Swarm 暂不纳入