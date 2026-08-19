"""P4 演示知识库：人事制度、财务流程与风控规则文档。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeDoc:
    title: str
    content: str
    source: str


# 内置演示知识库：后续可替换为真实制度文档或向量检索服务。
KNOWLEDGE_DOCS: tuple[KnowledgeDoc, ...] = (
    KnowledgeDoc(
        title="年假管理制度",
        source="hr/leave-policy.md",
        content=(
            "公司年假制度：累计工作满 1 年享受 5 天年假，满 10 年享受 10 天，"
            "满 20 年享受 15 天。年假申请需要通过 OA 提交，并提前 3 个工作日申请；"
            "休半天需在申请单中选择上午或下午。年假当年未休完可顺延至次年 3 月底；"
            "工龄自入职之日起累计计算。"
        ),
    ),
    KnowledgeDoc(
        title="试用期与转正制度",
        source="hr/recruitment-onboarding.md",
        content=(
            "试用期与转正制度：劳动合同期限 3 个月以上不满 1 年的试用期不超过 1 个月，"
            "1 年以上不满 3 年的不超过 2 个月，3 年以上或无固定期限的不超过 6 个月。"
            "社招转正需提交试用期考核、部门评价与 HR 复核材料；"
            "试用期工资不低于转正工资的 80%。"
        ),
    ),
    KnowledgeDoc(
        title="加班与调休制度",
        source="hr/overtime-compensation.md",
        content=(
            "加班与调休制度：工作日加班按 1.5 倍工资支付，周末加班优先安排调休或按 2 倍工资支付，"
            "法定节假日加班按 3 倍工资支付。"
        ),
    ),
    KnowledgeDoc(
        title="假期福利标准",
        source="hr/leave-benefits.md",
        content=(
            "假期福利标准：婚假 3 天，丧假 3 天，产假 158 天，陪产假 15 天，"
            "哺乳假每天 1 小时。"
        ),
    ),
    KnowledgeDoc(
        title="薪酬与社保福利",
        source="hr/salary-benefits.md",
        content=(
            "薪酬福利制度：社保缴费基数按上年度月平均工资确定，公积金缴存比例为 12%，"
            "高温补贴每年 6-9 月发放，每月 300 元。"
        ),
    ),
    KnowledgeDoc(
        title="离职交接流程",
        source="hr/exit-process.md",
        content=(
            "离职交接流程：员工提前 30 天提交离职申请，完成工作交接、资产归还、权限注销"
            "与离职面谈后方可办结。"
        ),
    ),
    KnowledgeDoc(
        title="绩效考核制度",
        source="hr/performance.md",
        content=(
            "绩效考核以季度为周期，结果用于晋升、调薪、培训与岗位调整；"
            "晋升需满足岗位年限要求，并通过部门提名与评审委员会复核。"
        ),
    ),
    KnowledgeDoc(
        title="员工持股计划",
        source="hr/esop.md",
        content=(
            "员工持股计划面向核心骨干员工开放，需满足司龄与绩效门槛，获授后按年度解锁。"
        ),
    ),
    KnowledgeDoc(
        title="差旅报销标准",
        source="finance/expense-policy.md",
        content=(
            "差旅报销标准：市内交通凭发票实报实销，住宿费按城市等级设置上限；"
            "报销单需附带行程说明、发票与审批记录，缺一不可。"
        ),
    ),
    KnowledgeDoc(
        title="培训报销制度",
        source="finance/training-reimbursement.md",
        content=(
            "培训报销制度：外部培训需提前审批，报销上限为每年度 5000 元，"
            "需提供培训合同、发票与结业证明。"
        ),
    ),
    KnowledgeDoc(
        title="出差补贴标准",
        source="finance/travel-allowance.md",
        content=(
            "出差补贴标准：国内出差每天补贴 120 元，住宿按城市等级上限报销，"
            "餐费凭发票实报实销。"
        ),
    ),
    KnowledgeDoc(
        title="合同付款风险条款",
        source="risk/contract-risk.md",
        content=(
            "合同审查重点关注付款节点、验收标准、违约金比例与争议解决条款；"
            "若付款节点与验收条款未绑定，属于高风险情形，需法务复核。"
        ),
    ),
    KnowledgeDoc(
        title="付款风险规则",
        source="risk/payment-risk.md",
        content=(
            "付款风险规则：付款节点必须与验收结果绑定；预付款比例超过 30% 需法务审批；"
            "付款先于验收属于高风险情形。"
        ),
    ),
    KnowledgeDoc(
        title="验收风险规则",
        source="risk/acceptance-risk.md",
        content=(
            "验收风险规则：验收标准应可量化并约定验收期限；仅写'验收合格'或未约定验收时限"
            "属于高风险情形。"
        ),
    ),
    KnowledgeDoc(
        title="违约金风险规则",
        source="risk/penalty-risk.md",
        content=(
            "违约金风险规则：违约金比例超过 30% 需法务复核；只约束乙方或双方违约金明显不对称"
            "属于风险提示项。"
        ),
    ),
    KnowledgeDoc(
        title="争议解决风险规则",
        source="risk/dispute-risk.md",
        content=(
            "争议解决风险规则：争议解决条款需明确仲裁机构或管辖法院；约定不明或仅写"
            "'协商解决'属于高风险情形。"
        ),
    ),
)


# 演示合同：结构与真实脱敏合同一致，后续可替换为合同解析/上传服务。
DEMO_CONTRACTS: dict[str, str] = {
    "demo": (
        "合同编号：HT-2026-DEMO-001\n"
        "付款条款：合同签订后 3 日内支付预付款 35%；验收合格后 30 日内支付剩余款项，未与验收结果绑定。\n"
        "验收条款：乙方完成交付后由甲方组织验收，验收标准为'验收合格'，未约定具体量化指标与验收期限。\n"
        "违约金条款：乙方逾期交付每日按合同金额 2% 支付违约金，甲方逾期付款不承担违约金。\n"
        "争议解决条款：双方协商解决，协商不成可向合同签订地人民法院起诉。\n"
    ),
}
