# Orchestra 第二阶段实施规划

> 状态：包 1 已实现（路由评测 89/89、拆解评测 6/6），包 2/包 3 待按序实施
>
> 包 1 落地：ScorerV2 特征/置信度、RoutingDecision 可解释因子、DecompositionPlanner + PlanValidator（场景模板 + LLM 规划，校验失败回退规则）、Simple 低置信/RAG 失败升级闭环、路由与拆解黄金用例与评测入口。
> 日期：2026-08-20
> 基线：P4.5 DAG + React 组合编排已落地（docs/07）

## 1. 目标与定位

第二阶段在已跑通的 MVP 闭环上补齐三类能力：

1. 路由与拆解底座：复杂度评分可解释、可校准，DAG 拆解可验证、可评测。
2. 真实 RAG 落地：从内存演示知识库升级为文档导入、Embedding、ChromaDB 与混合检索，并扩展到多部门 Agent。
3. 工作流引擎：从 SQLite + 进程内 asyncio 任务升级为 Redis Streams + 自研状态机，支持重试、恢复与多实例消费。

已确认的技术决策：

- 实施顺序：包 1 → 包 2 → 包 3。
- 向量数据库：ChromaDB，先使用本地持久化模式，后续可部署为服务。
- 工作流：Redis Streams + 自研状态机；Redis 未部署时保留 SQLite/内存驱动作为开发兜底。
- 外部依赖安装到 Conda 环境 `orchestra`（C:\Users\20235\.conda\envs\orchestra）。
- Redis、ChromaDB 等需要部署的组件编写 Docker 文档，由你手动部署。
- `.env` 预留第二阶段接口变量，包括 Embedding 模型与存储路径，实施时按包启用。

## 2. 目标架构

```text
接入层     REST / SSE / 管理 API
               |
编排层     RuleRouter v2 → RoutingDecision（分数＋置信度＋拆解计划）
               |
策略层     Simple / DAG / React / DAG+React 节点
               |
执行底座   Workspace / TokenBudget / RAGTool / WorkflowDriver
               |
数据层     SQLite 项目录 | ChromaDB 向量库 | Redis Streams 命令流/事件流
```

第二阶段新增组件：`ScorerV2`、`DecompositionPlanner`、`PlanValidator`、`IngestionService`、`RetrievalService`、`EmbeddingProvider`、`WorkflowDriver`、`EventBus`、`RetryScheduler`。

## 3. 包 1：路由与拆解底座

### 3.1 复杂度评分 v2

现状问题：

- 关键词加权只覆盖长度、连接词、步骤词、工具词，语义复杂度与歧义未建模。
- 全局固定阈值 0.3，不适合按部门调整，也无置信度。
- 路由结果缺少可解释特征，无法定位误路由原因。

目标设计：

```text
Input → feature extraction → score + confidence + reasons
            |-> 高置信区间：规则决策
            |-> 低置信/歧义区间：LLM 或 Embedding 分类复核
            └-> 决策落 RoutingDecision 与事件流
```

落地内容：

- `RoutingFeature`：文本长度、分句数、意图标记、工具依赖、部门上下文、已有工作区产物。
- 按场景输出阈值，例如 HR 单跳问答、风控条款审查、财务报销各有独立阈值。
- 增加简单问题失败后的升级闭环：Simple 结果置信不足时升级 React/DAG，流程可观测。
- 路由评测集覆盖人事、风控、财务、招采，目标 60-100 条，标注预期策略和阈值区间。

### 3.2 拆解计划与验证

现状问题：

- `split_parts` 依赖连接词切分，并行/串行判断过于粗糙。
- 通用 DAG 子任务不分配工具与节点策略，依赖场景配置补齐。
- 没有计划质量评测，无法判断是否拆漏、拆重、依赖错误。

目标设计：

- 新增 `DecompositionPlan`：任务、依赖、工具、节点策略、计划依据。
- 双通道规划器：部门模板命中时走确定模板；未命中时走 LLM 规划器，输出结构化 JSON。
- `PlanValidator` 校验：任务 ID 可引用、图无环、工具存在、角色合法、深度不超过 2 层。
- 新增拆解质量评测：任务召回率、依赖边 F1、计划合法率，与最终答案通过率分开统计。

### 3.3 验收标准

- 路由评测集达到 60 条以上，Mock + Openai 两档可重复执行。
- 路由准确率达到目标值（90% 为占位，验收后回填实测）。
- 拆解计划校验对环、缺依赖、非法工具可以明确报错。
- Simple 升级闭环有单元测试与 SSE 事件。

交付物：`docs/golden/routing-cases.json`、`ScorerV2`、`DecompositionPlanner`、`PlanValidator`、评测报告入口。

## 4. 包 2：真实 RAG 落地

### 4.1 文档导入与解析

- 支持 Markdown、PDF、Word、Excel、PPT 与纯文本。
- 按部门建立知识目录：`data/knowledge/{department}/*`，支持版本与来源元数据。
- 沉淀 `IngestionService`：解析、清洗、元数据抽取、索引状态记录。

### 4.2 分块

- 使用 Recursive Character Text Splitter，支持按 Markdown/表格结构切分。
- 块与块之间保留 `chunk_id`、`source`、`department`、`page`、`updated_at` 元数据。
- 编码固定 UTF-8，中文场景按语义边界切分，避免截断条款或表格。

### 4.3 Embedding

- 定义 `EmbeddingProvider` 接口，支持两种实现：
  - `openai`：复用 OpenAI 兼容接口，便于已有 API Key 快速接入。
  - `local`：使用 `sentence-transformers` 加载本地模型，默认 `BAAI/bge-small-zh-v1.5`。
- 模型下载依赖网络，Docker/部署文档中说明首次下载与离线缓存方案。

### 4.4 ChromaDB

- 向量库优先使用 ChromaDB 本地持久化模式，路径由 `ORCHESTRA_CHROMA_PATH` 控制。
- 每个部门一个 Collection 前缀，例如 `hr_policy`、`risk_rules`、`finance_policy`。
- 预留 ChromaDB Server 模式，`ORCHESTRA_CHROMA_HOST/PORT` 配置后切换客户端。

### 4.5 混合检索与 Rerank

- `RetrievalService` 同时运行关键词检索（BM25）与向量检索，按权重融合。
- 候选集 topN 默认 5-10 条，可选 Rerank 模型（如 `bge-reranker-base`）。
- 检索结果统一为 `RetrievedChunk`：文本、来源、相似度、命中位置。

### 4.6 证据与拒答

- RAG 回答强制带来源：`来源文档 + 命中片段`。
- 检索置信度低于阈值时明确拒答：`未检索到可依据的制度/规则文档`。
- 保留当前 Mock 知识库作为 CI 与演示兜底，生产切换真实文档。

### 4.7 部门 Agent 配置

- 人事：制度问答（Simple + RAG / React + RAG）。
- 风控：条款审查（DAG + React 节点，RAG + 合同上下文）。
- 财务：报销政策问答（Simple + RAG），单据校验（DAG + React，后续扩展）。
- 招采：合同条款问答与流程指引（Simple/DAG + RAG，后续扩展）。
- 部门 Agent 配置含数据范围、可用工具、策略模板、权限与评测集。

### 4.8 API 扩展

- `POST /api/v2/documents`：上传/导入知识文档。
- `GET /api/v2/documents`、`DELETE /api/v2/documents/{doc_id}`：管理文档索引。
- `POST /api/v2/knowledge/search`：检索接口，供调试与可视化。
- 保留现有 `/api/v1/tasks` 与 SSE 事件不变。

### 4.9 评测

- 每部门沉淀 15-30 条黄金用例。
- 指标：hit@k、MRR、溯源率、拒答率、任务通过率、P95 耗时、单任务 Token。
- 评测入口继续使用 `python -m orchestra.evals`，增加 `--department` 与 `--retrieval` 参数。

### 4.10 验收标准

- 至少人事、风控两个部门使用真实流程跑通，并保留 Mock 兜底。
- 检索命中率与溯源率达到目标占位（hit@5 >= 85%，溯源率 >= 90%）。
- 文档导入、索引、检索 API 可操作，ChromaDB 数据持久化可重启复用。
- `.env` 中 Embedding 与 ChromaDB 变量可切换。

## 5. 包 3：Redis Streams + 自研状态机

### 5.1 核心抽象

```text
WorkflowDriver 接口：submit / execute / retry / recover / finish
    |-> SqliteWorkflowDriver（开发/测试默认）
    └-> RedisStreamWorkflowDriver（生产部署）

EventBus 接口：publish / subscribe / replay
    |-> SqliteEventBus
    └-> RedisStreamEventBus

RetryScheduler 接口：schedule / cancel / pop_due
    └-> RedisZsetRetryScheduler（延迟队列）
```

### 5.2 Stream 设计

- 命令流：`orchestra:task:commands`，负责任务提交、节点执行、重试、恢复。
- 事件流：`orchestra:task:events`，负责可观测事件，SSE 可增量消费。
- 消费组：`orchestra-workers`，多个 Worker 并发消费，`XACK` 确认成功。
- 崩溃补偿：`XAUTOCLAIM` 认领超时未 ACK 消息。

### 5.3 延迟重试

- Redis Streams 不原生提供延迟队列，使用 Sorted Set 自研：`ZADD retry_time member`。
- 到期后通过 Lua 脚本原子弹出任务，再 `XADD` 到重试 Stream。
- 重试策略：`max_attempts`、指数退避、随机抖动、超时上限。
- Redisson 是 Java 客户端，本项目为 Python，不引入，使用自研 ZSET 方案。

### 5.4 状态机

```text
PENDING -> ROUTING -> RUNNING -> SUCCEEDED
                  |        |
                  +-> WAITING_DEPENDENCY / RETRYING -> RUNNING
                  +-> FAILED / CANCELLED
```

- 所有状态迁移带版本号与幂等校验，防止重复消费。
- 节点失败按策略重试；达到上限后进入 FAILED，并记录失败节点与原因。

### 5.5 崩溃恢复

- 启动时扫描未完成任务与 Redis Pending 消息，重新入队。
- 任务上下文从 SQLite 投影恢复，工作区从文件恢复。
- Redis 不可用时回退 SqliteWorkflowDriver，保证本地开发不阻塞。

### 5.6 SQLite 投影

- Redis 作为命令/事件源，SQLite 继续作为 API、SSE、历史查询的读模型。
- 后续需要 PostgreSQL 时，只需替换 SQLite 投影，不改变工作流接口。

### 5.7 验收标准

- DAG 节点执行失败可自动重试，指数退避生效，重试次数与原因可查询。
- 模拟 Worker 崩溃后，Pending 任务可被其他 Worker 认领并完成。
- 服务重启后未完成任务自动续跑。
- Redis 未部署环境下，SqliteWorkflowDriver 可运行全部现有测试。

## 6. 外部依赖

| 包 | 依赖 | 用途 |
| --- | --- | --- |
| 包 1 | 暂无强制新增；可选 `scikit-learn` | 规则置信度与离线阈值校准 |
| 包 2 | `chromadb`、`sentence-transformers`、`pypdf`、`python-docx`、`openpyxl`、`python-pptx`、`rank-bm25` | 文档解析、分块、Embedding、向量库、混合检索 |
| 包 3 | `redis>=5.0`；测试用 `fakeredis` | Redis Streams、延迟队列、事件流 |

依赖按包推进添加，统一安装到 Conda 环境 `orchestra`，并同步 `requirements*.txt`。

## 7. Docker 部署与环境准备

以下命令由你手动部署，实施到对应包时再落地为正式文档（`docs/09-deployment.md`）与 `docker-compose.phase2.yml`。

- Redis：`docker run -d --name orchestra-redis -p 6379:6379 -v orchestra_redis:/data redis:7.4-alpine`
- ChromaDB：`docker run -d --name orchestra-chroma -p 8001:8000 -v orchestra_chroma:/data chromadb/chroma`
- 本地开发不部署 Redis 时：`ORCHESTRA_WORKFLOW_DRIVER=sqlite`。
- ChromaDB 本地模式无需 Docker，`ORCHESTRA_CHROMA_PATH` 指向数据目录即可。
- Embedding 本地模型首次运行需要联网下载；也可使用 OpenAI 兼容接口，无需容器。

## 8. `.env` 预留变量

以下变量在第二阶段实施时逐步启用，先写入 `.env.example` 并注释：

| 变量 | 阶段 | 默认建议 | 说明 |
| --- | --- | --- | --- |
| `ORCHESTRA_ROUTING_GOLDEN_PATH` | 1 | `docs/golden/routing-cases.json` | 路由评测集路径 |
| `ORCHESTRA_ROUTING_AMBIGUOUS_BAND` | 1 | `0.25,0.35` | 低置信区间，触发复核 |
| `ORCHESTRA_HR_SCENARIO_THRESHOLD` | 1 | `0.30` | 人事场景独立阈值 |
| `ORCHESTRA_EMBEDDING_PROVIDER` | 2 | `openai` 或 `local` | Embedding 实现 |
| `ORCHESTRA_EMBEDDING_MODEL` | 2 | `BAAI/bge-small-zh-v1.5` | 本地模型名 |
| `ORCHESTRA_EMBEDDING_DIM` | 2 | `512` | 向量维度 |
| `ORCHESTRA_EMBEDDING_API_KEY` | 2 | 空 | API Embedding 密钥 |
| `ORCHESTRA_CHROMA_PATH` | 2 | `data/chroma` | 本地持久化目录 |
| `ORCHESTRA_CHROMA_HOST/PORT` | 2 | `127.0.0.1/8001` | ChromaDB Server 模式 |
| `ORCHESTRA_COLLECTION_PREFIX` | 2 | `orchestra` | Collection 前缀 |
| `ORCHESTRA_KNOWLEDGE_SOURCE_DIR` | 2 | `data/knowledge` | 原始文档目录 |
| `ORCHESTRA_RETRIEVAL_TOP_K` | 2 | `5` | 检索返回条数 |
| `ORCHESTRA_RETRIEVAL_MODE` | 2 | `hybrid` | `hybrid`/`vector`/`keyword` |
| `ORCHESTRA_RERANK_ENABLED` | 2 | `false` | 是否启用 Rerank |
| `ORCHESTRA_RERANK_MODEL` | 2 | `BAAI/bge-reranker-base` | Rerank 模型 |
| `ORCHESTRA_WORKFLOW_DRIVER` | 3 | `sqlite` | `sqlite`/`redis` |
| `ORCHESTRA_REDIS_URL` | 3 | `redis://127.0.0.1:6379/0` | Redis 连接地址 |
| `ORCHESTRA_REDIS_STREAM_PREFIX` | 3 | `orchestra` | Stream Key 前缀 |
| `ORCHESTRA_REDIS_CONSUMER_GROUP` | 3 | `orchestra-workers` | 消费组名称 |
| `ORCHESTRA_WORKER_CONCURRENCY` | 3 | `4` | 单 Worker 并发数 |
| `ORCHESTRA_RETRY_MAX_ATTEMPTS` | 3 | `3` | 节点最大重试次数 |
| `ORCHESTRA_RETRY_BASE_DELAY_MS` | 3 | `1000` | 首次重试延迟 |
| `ORCHESTRA_RETRY_MAX_DELAY_MS` | 3 | `60000` | 重试延迟上限 |
| `ORCHESTRA_RETRY_JITTER_MS` | 3 | `200` | 退避随机抖动 |

## 9. 里程碑与节奏

| 阶段 | 周期 | 入口 | 出口 |
| --- | --- | --- | --- |
| 包 1：路由与拆解底座 | 约 2-3 周 | 扩展现有 Router/evals | 路由回归通过，拆解计划可验证 |
| 包 2：真实 RAG 落地 | 约 2-3 周 | 依赖包 1 评测集 | 人事/风控真实 RAG 链路通过 |
| 包 3：Redis Streams 工作流 | 约 3 周 | 依赖包 2 稳定链路 | 重试/恢复/多 Worker 验收通过 |

## 10. 风险与预案

| 风险 | 影响 | 预案 |
| --- | --- | --- |
| Redis 暂未部署 | 工作流无法真实验收 | 保留 SqliteWorkflowDriver，接口先行 |
| Embedding 模型下载受限 | 真实 RAG 无法索引 | 优先 OpenAI 兼容接口，本地模型作为可选项 |
| 真实业务文档不足 | 部门评测质量受限 | 先用脱敏样例与黄金用例，权限到位后替换 |
| ChromaDB 单机容量 | 文档量大后检索退化 | 预留 ChromaDB Server 与按部门 Collection 分片 |
| 延迟队列自研复杂度 | 重试调度边界问题 | 先用 ZSET + Lua 的最小实现，再扩展可视化 |

## 11. 预期成果与简历量化占位

- 路由准确率目标：`>= 90%`（验收后回填实测）。
- 检索效果目标：`hit@5 >= 85%`、`MRR >= 0.8`。
- 部门 Agent 通过率：人事/风控按黄金用例分别回填。
- 工作流稳定性：节点重试成功率、故障恢复时间、重复执行率回填实测。

## 12. 相关文档

- [docs/07-dag-react-composition.md](docs/07-dag-react-composition.md)：P4.5 组合编排基线
- [docs/02-technology-selection.md](docs/02-technology-selection.md)：技术选型
- [docs/05-business-scenarios.md](docs/05-business-scenarios.md)：业务场景清单
- [docs/06-development-environment.md](docs/06-development-environment.md)：开发环境

