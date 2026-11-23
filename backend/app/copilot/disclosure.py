"""防胡编强制披露闸门（Copilot 层 Layer 5）。

借鉴 `sql_explorer` 的 `disclose=True` + 逐字披露纪律：
凡底层系统逻辑尚未包含、存在口径分歧、或尚未经过业务签字确认的内容，工具必须显式携带
`disclose=True` 与固定披露文本，并在回答正文开头原样呈现，严禁用流畅的业务话术掩盖事实缺失。
"""

from typing import Any, Dict

DISCLOSURE_TEXT: Dict[str, str] = {
    "unrealized": (
        "【逻辑覆盖说明：当前系统逻辑尚未包含】监管或业务制度文档中提出了以下要求，"
        "但在底层系统处理逻辑中暂未检索到对应实现，以下内容仅为制度规范要求，不代表系统已自动执行。"
    ),
    "divergent": (
        "【逻辑差异说明：系统内部存在多条并行口径】底层系统处理逻辑中检测到多种不尽相同的实现路径，"
        "以下并列展示各自触发的业务场景与对应的业务解释，供业务团队了解差异来源。"
    ),
    "not_found": (
        "【覆盖范围说明：未检索到匹配业务逻辑】在当前已接入的系统逻辑与制度材料中，"
        "未发现与该问题直接对应的业务规则或指标口径。"
    ),
    "draft_only": (
        "【业务解释来源说明：由系统逻辑自动提炼】以下内容由系统从底层处理逻辑与制度材料中自动还原生成，"
        "已有实际系统逻辑支撑，如发现业务理解偏差可通过“优化业务解释”补充完善。"
    ),
}


def build_disclosure(kind: str, subject: str = "") -> Dict[str, Any]:
    text = DISCLOSURE_TEXT.get(kind, "【事实状态提示】")
    if subject:
        text = f"{text}（涉及事项：{subject}）"
    return {
        "disclose": True,
        "disclose_kind": kind,
        "disclose_text": text,
    }
