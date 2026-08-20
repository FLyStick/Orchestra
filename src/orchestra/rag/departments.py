"""部门归一化：把中文部门名/混写别名统一为向量 Collection 的英文标识。"""
from __future__ import annotations

_DEPARTMENT_ALIASES = {
    "hr": "hr",
    "人事": "hr",
    "人力": "hr",
    "员工关系": "hr",
    "risk": "risk",
    "风控": "risk",
    "法务": "risk",
    "合规": "risk",
    "finance": "finance",
    "财务": "finance",
    "报销": "finance",
    "procurement": "procurement",
    "招采": "procurement",
    "采购": "procurement",
    "供应商管理": "procurement",
}


def normalize_department(value: str | None) -> str:
    """把部门输入归一化为稳定标识，未识别时按小写原值返回。"""
    if not value:
        return "general"
    key = str(value).strip().lower()
    return _DEPARTMENT_ALIASES.get(key, key)
