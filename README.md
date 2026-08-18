# Orchestra

面向公司内部的多智能体编排框架，借鉴 Shannon 开源框架的架构思想，落地统一的任务拆解、策略路由、工作流持久化、多智能体协作与成本控制能力。

## 阶段状态

| 阶段 | 交付物 | 状态 |
|---|---|---|
| P0 方案设计 | 多智能体编排框架实现方案.md | 已完成 |
| P1 调研设计 | docs/ 与 src/orchestra/contracts | 已完成 |
| P2 最小闭环 | FastAPI API + 规则路由 + Simple/DAG + SQLite + SSE | 已完成（待安装依赖后运行） |
| P3 能力增强 | React + Workspace + Token 预算 | 待启动 |
| P4 原型与验证 | 2-3 个业务 Agent 原型及评测 | 待启动 |

## 环境准备

Conda 环境：`orchestra`。安装与运行步骤见 [docs/06-development-environment.md](docs/06-development-environment.md)。

```powershell
conda activate orchestra
cd D:\实习记录\组内项目\Orchestra
pip install -r requirements-dev.txt
pip install -e .
python -m orchestra.main
```

## 目录结构

```text
docs/                            P1/P2 设计文档与开发环境文档
src/orchestra/
  api.py                         FastAPI 入口，REST/SSE 接口
  executor.py                    任务执行器
  router.py                      规则路由与复杂度评分
  store.py                       SQLite 任务/事件/Token 存储
  llm.py                         Mock 与 OpenAI 兼容 Provider
  contracts/                     核心数据契约与策略接口
  strategies/                    Simple/DAG 策略
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

## 开发验证

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```