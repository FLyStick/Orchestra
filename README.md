# Orchestra

面向公司内部的多智能体编排框架，借鉴 Shannon 开源框架的架构思想，落地统一的任务拆解、策略路由、工作流持久化、多智能体协作与成本控制能力。

## 阶段状态

| 阶段 | 交付物 | 状态 |
|---|---|---|
| P0 方案设计 | 多智能体编排框架实现方案.md | 已完成 |
| P1 调研设计 | docs/ 与 src/orchestra/contracts | 已完成 |
| P2 最小闭环 | API + Simple/DAG 执行 + 事件流 | 待启动 |
| P3 能力增强 | React + Workspace + Token 预算 | 待启动 |
| P4 原型与验证 | 2-3 个业务 Agent 原型及评测 | 待启动 |

## 目录结构

```text
docs/                            P1 调研设计文档
src/orchestra/contracts/         核心数据契约与策略接口
tests/                           契约冒烟测试
多智能体编排框架实现方案.md       总体方案与实习经历条目
```

## 文档索引

- docs/01-shannon-research.md  Shannon 架构调研
- docs/02-technology-selection.md  技术选型决策记录
- docs/03-architecture.md  总体架构设计
- docs/04-api-design.md  API 与接口设计
- docs/05-business-scenarios.md  业务场景清单与验收口径

## 开发验证

P1 契约代码仅依赖 Python 标准库：

```bash
python -m compileall src
python -m unittest discover -s tests -v
```

运行测试前设置 `PYTHONPATH` 指向 `src`，或在项目中执行 `pip install -e .`。