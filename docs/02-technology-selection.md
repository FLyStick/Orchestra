# 技术选型决策记录

> 决策时间：2026-08-18
> 状态：P1 已确认，P2 落地时按环境调整

## 1. 总体实现方式

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| 直接部署 Shannon | 开箱即用、能力完整 | 依赖 Go/Rust/WASI 等重组件，与公司内部轻量场景不匹配 | 不采纳 |
| 基于 LangGraph 扩展 | 团队熟悉、Agent 生态丰富 | 编排状态与故障恢复需要自行补充 | 作为备选 |
| 借鉴 Shannon 架构自研 Orchestra | 裁剪灵活、可控性强、简历主线清晰 | 需要自己实现核心编排 | 采纳 |

核心选择：只借鉴架构，不直接复制源码；P2 从最小闭环开始。

## 2. 工作流引擎

| 方案 | 优点 | 缺点 |
| --- | --- | --- |
| Temporal | 持久化、重试、信号、时间旅行调试 | 需要部署 Server |
| Celery | 简单、Python 生态 | 长任务状态与恢复能力弱 |
| Redis Streams + 自研状态机 | 轻量、可控 | 重试/恢复/调试需要自研 |

结论：优先 Temporal；若公司环境不可用，降级为 Redis Streams + 自研状态机，但需保留相同接口边界。

## 3. 语言与框架

- 语言：Python 3.11+
- API 框架：FastAPI
- 理由：与 AgentChat 经验一致、异步生态成熟、模型调用与工具开发效率高

## 4. 协议

- 对外主协议：REST + SSE
- 内部扩展协议：gRPC（仅在需要强类型 RPC 或跨语言服务时引入）
- 理由：REST 降低接入成本，SSE 满足流式事件，gRPC 不作为 P2 阻塞项

## 5. 存储

| 组件 | 用途 |
| --- | --- |
| Redis | Workspace 中间结果、事件流、缓存、分布式锁 |
| PostgreSQL/MySQL | 任务表、事件表、token_usage、评测记录 |

## 6. 模型与检索

- LLM：OpenAI API / 开源模型，通过统一 Provider 接口接入
- 模型降级：预算超限或调用失败时按配置降级
- 检索：复用 AgentChat 的 Milvus / ES / ChromaDB + RAG 链路，不重复建设

## 7. 决策汇总

| ID | 决策 | 状态 | 替代方案 |
| --- | --- | --- | --- |
| ADR-001 | 借鉴 Shannon 架构自研 | 已确认 | 直接部署 Shannon、LangGraph 扩展 |
| ADR-002 | 工作流引擎使用 Temporal | 已确认 | Redis Streams + 自研状态机 |
| ADR-003 | Python + FastAPI | 已确认 | Go、Node.js |
| ADR-004 | REST + SSE 先行 | 已确认 | gRPC 为主 |
| ADR-005 | Redis + PostgreSQL | 已确认 | 仅文件系统 + SQLite（原型期） |
| ADR-006 | 复用现有 RAG 检索能力 | 已确认 | 自研检索服务 |