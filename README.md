# Orchestra

面向公司内部的多智能体编排框架，借鉴 Shannon 开源框架的架构思想，落地统一的任务拆解、策略路由、工作流持久化、多智能体协作与成本控制能力。

## 阶段状态

| 阶段 | 交付物 | 状态 |
|---|---|---|
| P0 方案设计 | 多智能体编排框架实现方案.md | 已完成 |
| P1 调研设计 | docs/ 与 src/orchestra/contracts | 已完成 |
| P2 最小闭环 | FastAPI API + 规则路由 + Simple/DAG + SQLite + SSE | 已完成 |
| P3 能力增强 | React 工具循环 + RAG + Workspace + Token 预算降级 | 已完成 |
| P4 原型与验证 | 人事制度问答 + 风控条款审查 2 个业务原型、30 条黄金用例评测器 | 已完成（量化数据待实测回填） |
| P4.5 组合编排 | DAG + React 正交化、节点级策略、Simple+RAG/React 路由 | 已完成 |
| 第二阶段·包 1 | 路由评测底座、可解释评分、拆解计划校验 | 已实现（路由评测 89/89） |
| 第二阶段·包 2 | 真实 RAG 落地：文档导入、ChromaDB、混合检索/Rerank、RAG CLI/API | 已实现（真实链路已跑通） |
| 第二阶段·包 3 | Redis Streams + 自研状态机工作流引擎：重试、恢复、多 Worker、事件总线 | 已实现（全量测试 62 项通过 + 真实 Redis 验收） |

## 环境准备

Conda 环境：`orchestra`。安装与运行步骤见 [docs/06-development-environment.md](docs/06-development-environment.md)。

```powershell
conda activate orchestra
cd D:\实习记录\组内项目\Orchestra
Copy-Item .env.example .env
pip install -r requirements-dev.txt
pip install -e .
python -m orchestra.main
```
## 接口路由

API 文档：http://127.0.0.1:8000/docs
健康检查：http://127.0.0.1:8000/healthz
默认 Mock LLM（无需 API Key 即可跑通全流程）；.env 里配了真实 Key 则走 OpenAI 兼容接口（当前是阿里云百炼 qwen3.7-flash）

**快速验证**

1. 提交任务（返回 task_id）

curl -X POST http://127.0.0.1:8000/api/v1/tasks `
  -H "Content-Type: application/json" `
  -d '{\"query\": \"报销标准是什么\", \"session_id\": \"demo-1\"}'

2. 查询任务结果

curl http://127.0.0.1:8000/api/v1/tasks/<task_id>

3. 订阅事件流（SSE）

curl -N http://127.0.0.1:8000/api/v1/tasks/<task_id>/events

## 目录结构

```text
docs/                            P1-P4 设计文档与开发环境文档
docker/                         Redis / ChromaDB Docker 编排与手动部署文档
src/orchestra/
  api.py                         FastAPI 入口，REST/SSE 接口
  executor.py                    任务执行器
  router.py                      ScorerV2 场景路由与置信度评分
  planning.py                    拆解规划与计划校验
  store.py                       SQLite 任务/事件/Token 存储
  llm.py                         Mock 与 OpenAI 兼容 Provider
  budget.py                      Token 总预算与模型降级
  tools.py                       RAG/合同/Workspace 工具注册
  rag_cli.py                     RAG 导入/检索/管理 CLI
  rag/                           RAG 解析、Embedding、ChromaDB、检索服务
  knowledge.py                   P4 演示制度与合同知识库
  scenarios.py                   业务场景与 DAG 子任务配置
  evals.py                       P4 黄金用例 + 路由/拆解评测
  contracts/                     核心数据契约与策略接口
  strategies/                    Simple/DAG/React 策略
  workflow/                      Redis Streams + 自研状态机、Worker 与重试调度
  workspace/                     本地文件与内存 Workspace
tests/                           单元测试与 API 集成测试
多智能体编排框架实现方案.md       总体方案与实习经历条目
```

## 文档索引

- docs/01-shannon-research.md  Shannon 架构调研
- docs/02-technology-selection.md  技术选型决策记录
- docs/03-architecture.md  总体架构设计
- docs/04-api-design.md  API 与接口设计
- docs/05-business-scenarios.md  业务场景清单与验收口径
- docs/06-development-environment.md  开发环境与部署文档
- docs/07-dag-react-composition.md  DAG + React 组合编排设计（已实施）
- docs/08-phase2-plan.md  第二阶段实施规划（包 1/2/3 已实现）
- docs/08-package1-technical-design.md  包 1 技术设计（路由与拆解底座）
- docs/09-package2-report.md  包 2 实施报告
- docs/09-package2-technical-design.md  包 2 技术设计（真实 RAG）
- docs/10-package3-report.md  包 3 实施报告
- docs/11-package3-technical-design.md  包 3 技术设计（工作流引擎）
- docs/12-technical-documentation.md  项目全量技术文档
- docker/README.md  Redis / ChromaDB Docker 手动部署

## 开发验证

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v

# Mock 评测（不消耗真实 Token）
python -m orchestra.evals --provider mock

# 路由/拆解评测（纯规则，不消耗 Token）
python -m orchestra.evals --mode routing
python -m orchestra.evals --mode decomposition
```
