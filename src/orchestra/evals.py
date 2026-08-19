"""P4 评测器：人事制度问答黄金用例跑分。

目标通过率 87%（26/30）与风控审查耗时 45 分钟 -> 8 分钟均为验收目标占位；
本模块输出的 pass_rate / P95 耗时是实际执行结果，项目验收后回填简历口径。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .contracts.task import TaskInput
from .executor import Executor
from .llm import LLMService, create_llm_provider
from .router import RuleRouter
from .store import SQLiteStore
from .workspace.local_workspace import LocalWorkspace


@dataclass(frozen=True)
class GoldenCase:
    """一条评测黄金用例：问题 + 期望命中的知识文档来源。"""

    query: str
    expected_source: str
    expected_keywords: tuple[str, ...] = ()
    department: str = "hr"


# P4 首批 30 条人事制度问答黄金用例，覆盖年假、转正、加班、福利、离职等主题。
GOLDEN_CASES: tuple[GoldenCase, ...] = (
    GoldenCase("公司年假有几天", "rag/hr/leave-policy.md", ("5天",)),
    GoldenCase("累计工作满10年年假多少天", "rag/hr/leave-policy.md", ("10天",)),
    GoldenCase("年假申请需要提前几天", "rag/hr/leave-policy.md", ("3个工作日",)),
    GoldenCase("年假未休完可以顺延吗", "rag/hr/leave-policy.md", ("次年3月底",)),
    GoldenCase("试用期最长不能超过几个月", "rag/hr/recruitment-onboarding.md", ("6个月",)),
    GoldenCase("社招转正需要提交哪些材料", "rag/hr/recruitment-onboarding.md", ("试用期考核",)),
    GoldenCase("试用期工资不得低于转正工资的多少", "rag/hr/recruitment-onboarding.md", ("80%",)),
    GoldenCase("工作日加班工资按几倍支付", "rag/hr/overtime-compensation.md", ("1.5倍",)),
    GoldenCase("法定节假日加班按几倍工资", "rag/hr/overtime-compensation.md", ("3倍",)),
    GoldenCase("周末加班可以调休还是支付2倍工资", "rag/hr/overtime-compensation.md", ("调休",)),
    GoldenCase("婚假有几天", "rag/hr/leave-benefits.md", ("3天",)),
    GoldenCase("产假多少天", "rag/hr/leave-benefits.md", ("158天",)),
    GoldenCase("陪产假多少天", "rag/hr/leave-benefits.md", ("15天",)),
    GoldenCase("哺乳假每天多长", "rag/hr/leave-benefits.md", ("1小时",)),
    GoldenCase("社保缴费基数如何确定", "rag/hr/salary-benefits.md", ("上年度月平均工资",)),
    GoldenCase("公积金缴存比例是多少", "rag/hr/salary-benefits.md", ("12%",)),
    GoldenCase("薪酬福利制度中高温补贴每年哪几个月发放", "rag/hr/salary-benefits.md", ("6-9月",)),
    GoldenCase("离职申请需要提前多少天", "rag/hr/exit-process.md", ("30天",)),
    GoldenCase("离职交接需要完成哪些事项", "rag/hr/exit-process.md", ("权限注销",)),
    GoldenCase("绩效考核周期是多久", "rag/hr/performance.md", ("季度",)),
    GoldenCase("绩效考核结果用于哪些方面", "rag/hr/performance.md", ("晋升",)),
    GoldenCase("员工持股计划制度面向哪些员工", "rag/hr/esop.md", ("核心骨干",)),
    GoldenCase("外部培训报销年度上限是多少", "rag/finance/training-reimbursement.md", ("5000元",)),
    GoldenCase("培训报销需要提供哪些证明", "rag/finance/training-reimbursement.md", ("结业证明",)),
    GoldenCase("公司制度中差旅报销需要附带哪些材料", "rag/finance/expense-policy.md", ("发票",)),
    GoldenCase("公司制度规定国内出差每天补贴多少", "rag/finance/travel-allowance.md", ("120元",)),
    GoldenCase("劳动合同期限三年试用期不超过几个月", "rag/hr/recruitment-onboarding.md", ("2个月",)),
    GoldenCase("年假工龄满20年可以休多少天", "rag/hr/leave-policy.md", ("15天",)),
    GoldenCase("公司绩效制度中晋升需要满足哪些条件", "rag/hr/performance.md", ("岗位年限",)),
    GoldenCase("高温补贴制度规定每月多少元", "rag/hr/salary-benefits.md", ("300元",)),
)


@dataclass
class CaseResult:
    """单条用例的评测结果。"""

    index: int
    query: str
    passed: bool
    strategy: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    reason: str


@dataclass
class EvalReport:
    """一次评测运行的汇总报告。"""

    total: int
    passed: int
    pass_rate: float
    target_pass_rate: float
    target_passed: int
    p95_latency_ms: float
    avg_latency_ms: float
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    duration_seconds: float
    note: str
    cases: list[CaseResult] = field(default_factory=list)


def _p95(values: list[int]) -> float:
    """计算 P95 耗时；样本为空时返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return float(ordered[index])


async def evaluate_cases(
    settings: Settings,
    cases: tuple[GoldenCase, ...] = GOLDEN_CASES,
    limit: int | None = None,
) -> EvalReport:
    """按黄金用例运行端到端评测，返回通过率、P95 耗时与 Token 用量。

    Args:
        settings: 运行配置（provider 为 mock 时不消耗真实 Token）。
        cases: 黄金用例集，默认使用内置 30 条。
        limit: 最多执行条数，测试与调试时可截断。

    Returns:
        EvalReport 汇总报告。
    """
    selected = list(cases[:limit] if limit is not None else cases)
    started_total = time.perf_counter()
    case_results: list[CaseResult] = []
    # 每次评测使用独立临时目录，避免污染本地 data 工作区。
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = SQLiteStore(root / "eval.db")
        provider = create_llm_provider(settings)
        llm_service = LLMService(provider, settings.llm_model, settings.fallback_model)
        executor = Executor(
            store=store,
            llm_service=llm_service,
            router=RuleRouter(),
            workspace_root=root / "workspaces",
        )
        for index, case in enumerate(selected):
            session_id = f"eval-{index}"
            started = time.perf_counter()
            output = await executor.execute_sync(
                TaskInput(
                    query=case.query,
                    session_id=session_id,
                    user_id="eval",
                    context={"department": case.department},
                )
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            workspace = LocalWorkspace(root / "workspaces", session_id)
            files = await workspace.list_files()
            answer = await workspace.read("answer.md")
            status = str(output.get("status") or "")
            strategy = str(output.get("strategy") or "")
            source_hit = case.expected_source in files
            # P4.5 起 HR 简单问题走 Simple+RAG，复杂问题走 React+RAG；
            # 只要命中知识来源且成功生成答案，两种通道均计入通过。
            passed = (
                status == "succeeded"
                and strategy in ("simple", "react")
                and source_hit
                and bool(answer)
            )
            reasons: list[str] = []
            if status != "succeeded":
                reasons.append(f"status={status}")
            if strategy not in ("simple", "react"):
                reasons.append(f"strategy={strategy}")
            if not source_hit:
                reasons.append(f"missing_source={case.expected_source}")
            if not answer:
                reasons.append("empty_answer")
            usage = output.get("token_usage") or {}
            case_results.append(
                CaseResult(
                    index=index,
                    query=case.query,
                    passed=passed,
                    strategy=strategy,
                    latency_ms=latency_ms,
                    input_tokens=int(usage.get("input_tokens", 0)),
                    output_tokens=int(usage.get("output_tokens", 0)),
                    reason="; ".join(reasons) if reasons else "ok",
                )
            )
        store.close()
    duration_seconds = round(time.perf_counter() - started_total, 3)
    passed_count = sum(1 for item in case_results if item.passed)
    total_input = sum(item.input_tokens for item in case_results)
    total_output = sum(item.output_tokens for item in case_results)
    latencies = [item.latency_ms for item in case_results]
    return EvalReport(
        total=len(case_results),
        passed=passed_count,
        pass_rate=round(passed_count / len(case_results), 4) if case_results else 0.0,
        target_pass_rate=0.87,
        target_passed=26,
        p95_latency_ms=_p95(latencies),
        avg_latency_ms=round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_input + total_output,
        duration_seconds=duration_seconds,
        note="P4.5 起 HR 走 Simple+RAG / React+RAG 双通道；87%（26/30）为验收占位，本报告为实际执行结果。",
        cases=case_results,
    )


def main() -> None:
    """命令行入口：python -m orchestra.evals --provider mock|openai。"""
    parser = argparse.ArgumentParser(description="Orchestra P4 黄金用例评测器")
    parser.add_argument(
        "--provider",
        choices=("mock", "openai"),
        default="mock",
        help="LLM Provider，mock 不消耗真实 Token",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=len(GOLDEN_CASES),
        help="最多执行的用例数，默认全部",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="报告 JSON 输出路径，默认仅打印到标准输出",
    )
    args = parser.parse_args()
    settings = Settings(llm_provider=args.provider)
    report = asyncio.run(evaluate_cases(settings, limit=args.max_cases))
    payload: dict[str, Any] = asdict(report)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
