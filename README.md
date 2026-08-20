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
| 第二阶段规划 | 路由评测底座、真实 RAG、Redis Streams 工作流 | 规划完成（待实施） |

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

## 目录结构

```text
docs/                            P1-P4 设计文档与开发环境文档
src/orchestra/
  api.py                         FastAPI 入口，REST/SSE 接口
  executor.py                    任务执行器
  router.py                      规则路由与 React/DAG 拆分
  store.py                       SQLite 任务/事件/Token 存储
  llm.py                         Mock 与 OpenAI 兼容 Provider
  budget.py                      Token 总预算与模型降级
  tools.py                       RAG/合同/Workspace 工具注册
  knowledge.py                   P4 演示制度与合同知识库
  scenarios.py                   业务场景与 DAG 子任务配置
  evals.py                       30 条黄金用例评测器
  contracts/                     核心数据契约与策略接口
  strategies/                    Simple/DAG/React 策略
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
- docs/08-phase2-plan.md  第二阶段实施规划（待实施）

## 开发验证

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v

# Mock 评测（不消耗真实 Token）
python -m orchestra.evals --provider mock
```