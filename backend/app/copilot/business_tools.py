"""7 个纯中文业务问答受控工具集（Copilot 层 Layer 5）。

严格遵守「代码隐身，业务显形」红线：
- 只检索业务资产层与封装后的底层实证支撑结论，绝不吐出任何物理表名、字段英文名或 SQL；
- 遇到未落地（UNREALIZED）、口径分歧（DIVERGENT）或无匹配时强制附带 `disclose` 披露标识。
"""

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.catalog.views import (
    list_business_calibers,
    list_business_rules,
    list_capabilities,
    list_reconcile_ledger,
)
from app.copilot.disclosure import build_disclosure
from app.policy.dc_bus import sanitize_business_text

BUSINESS_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "list_covered_businesses",
        "description": "盘点系统逻辑里已经包含的全部业务板块、规则数量与覆盖落地状态，用于回答“我们的逻辑里面包含了哪些业务”。",
    },
    {
        "name": "resolve_business_concept",
        "description": "把业务人员的日常问法映射到对应的业务板块、处理规则或指标口径卡。",
    },
    {
        "name": "get_business_scope_and_rules",
        "description": "查询某项业务或规则下具体的纳入范围、剔除情形、分支处理与防重规则（纯业务语言）。",
    },
    {
        "name": "get_business_caliber_detail",
        "description": "查询某个业务指标（如交易金额、交易后余额、联名账户余额等）的定义、算法口径与防混淆注意事项。",
    },
    {
        "name": "verify_implementation_backing",
        "description": "核实某条业务规则或指标在系统底层逻辑中是否确有实现支撑与制度对账结论（绝不返回表名或代码）。",
    },
    {
        "name": "check_regulatory_coverage_gap",
        "description": "对比制度材料与当前系统逻辑，盘点已一致落地、存在多套分歧以及尚未被系统包含的差距清单。",
    },
    {
        "name": "find_rules_by_business_role",
        "description": "按业务岗位（如业务、监管报送、合规、财务核算）筛选该岗位最关心的业务规则与口径。",
    },
]


def _score_match(query: str, *fields: str) -> int:
    q = (query or "").strip()
    if not q:
        return 0
    hay = " ".join(f for f in fields if f)
    if q in hay:
        return 120
    primary_field = " ".join(f for f in fields[:2] if f)
    domain_keywords = (
        "应报未报", "漏报", "补报", "结售汇", "购汇", "结汇", "资金来源", "资金使用",
        "30万", "变动编号", "交易对手", "总账", "开户行", "个体工商户", "迁移",
        "对公客户", "捞回", "境内外", "居民", "机构信息", "银行机构", "营业执照", "金融代码", "上级机构",
        "授信额度", "授信", "测试客户", "测试", "虚拟", "港澳通行证", "信用卡",
        "分户账", "个人存款", "客户归并", "交易后余额", "交易金额", "联名", "折算",
        "离岸", "转口贸易", "延期", "个人卡", "对公", "统括", "去重",
        "数字人民币", "一表通", "互联网贷款", "资产转让", "大额", "可疑", "外汇", "余额",
    )
    generic_keywords = ("报送", "范围", "纳入", "剔除", "排除", "重复", "合规", "分歧", "未包含")
    score = 0
    for w in domain_keywords:
        if w in q and w in hay:
            score += 65 if w in primary_field else 20
    for w in generic_keywords:
        if w in q and w in hay:
            score += 8 if w in primary_field else 4
    return score


def execute_business_tool(session: Session, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name == "list_covered_businesses":
        caps = list_capabilities(session)
        covered = [c for c in caps if c["coverage_status"] in {"COVERED", "PARTIAL"}]
        unrealized = [c for c in caps if c["coverage_status"] == "UNREALIZED"]
        out: Dict[str, Any] = {
            "tool": tool_name,
            "covered_businesses": [
                {
                    "code": c["code"],
                    "name": c["name"],
                    "description": sanitize_business_text(c["description"]),
                    "scope_summary": sanitize_business_text(c["scope_summary"]),
                    "coverage_status": c["coverage_status"],
                    "rule_count": c["rule_count"],
                    "caliber_count": c["caliber_count"],
                    "confirmed_count": c["confirmed_count"],
                    "audience": c["audience"],
                }
                for c in covered
            ],
            "unrealized_businesses": [
                {
                    "code": c["code"],
                    "name": c["name"],
                    "description": sanitize_business_text(c["description"]),
                    "coverage_status": c["coverage_status"],
                }
                for c in unrealized
            ],
        }
        if not caps:
            out.update(build_disclosure("not_found"))
        elif unrealized:
            out.update(build_disclosure("unrealized", "、".join(c["name"] for c in unrealized)))
        return out

    if tool_name == "resolve_business_concept":
        query = str(args.get("concept") or args.get("query") or "")
        caps = list_capabilities(session)
        rules = list_business_rules(session)
        calibers = list_business_calibers(session)
        matched_caps = sorted(
            [c for c in caps if _score_match(query, c["name"], c["description"], c["scope_summary"]) > 0],
            key=lambda x: _score_match(query, x["name"], x["description"], x["scope_summary"]),
            reverse=True,
        )[:4]
        matched_rules = sorted(
            [r for r in rules if _score_match(query, r["title"], r["business_condition"], r["business_action"]) > 0],
            key=lambda x: _score_match(query, x["title"], x["business_condition"], x["business_action"]),
            reverse=True,
        )[:5]
        matched_cals = sorted(
            [c for c in calibers if _score_match(query, c["metric_name"], c["business_definition"], " ".join(c["triggers"])) > 0],
            key=lambda x: _score_match(query, x["metric_name"], x["business_definition"], " ".join(x["triggers"])),
            reverse=True,
        )[:4]
        res: Dict[str, Any] = {
            "tool": tool_name,
            "capabilities": matched_caps,
            "rules": matched_rules,
            "calibers": matched_cals,
        }
        if not (matched_caps or matched_rules or matched_cals):
            res.update(build_disclosure("not_found", query))
        return res

    if tool_name == "get_business_scope_and_rules":
        topic = str(args.get("topic") or args.get("query") or "").strip()
        category = args.get("rule_category")
        rules = list_business_rules(session, rule_category=category)
        if topic:
            scored = [(r, _score_match(topic, r["title"], r["capability_name"], r["business_condition"], r["business_action"])) for r in rules]
            max_sc = max((sc for _, sc in scored), default=0)
            cutoff = max(18, int(max_sc * 0.45)) if max_sc >= 20 else 1
            filtered = [r for r, sc in sorted(scored, key=lambda x: x[1], reverse=True) if sc >= cutoff]
            if filtered:
                rules = filtered
            elif not any(g in topic for g in ("全部", "所有", "列表")):
                rules = []
        out = {
            "tool": tool_name,
            "rules": [
                {
                    "code": r["code"],
                    "title": sanitize_business_text(r["title"]),
                    "capability_name": r["capability_name"],
                    "rule_category": r["rule_category"],
                    "business_condition": sanitize_business_text(r["business_condition"]),
                    "business_action": sanitize_business_text(r["business_action"]),
                    "business_rationale": sanitize_business_text(r["business_rationale"]),
                    "reconcile_status": r["reconcile_status"],
                    "status": r["status"],
                }
                for r in rules
            ],
        }
        if not rules:
            out.update(build_disclosure("not_found", topic))
        elif any(r["reconcile_status"] == "UNREALIZED" for r in rules[:6]):
            out.update(build_disclosure("unrealized", "部分待落地规则"))
        elif any(r["reconcile_status"] == "DIVERGENT" for r in rules[:6]):
            out.update(build_disclosure("divergent", "分支处理口径分歧"))
        return out

    if tool_name == "get_business_caliber_detail":
        metric = str(args.get("metric") or args.get("query") or "").strip()
        calibers = list_business_calibers(session)
        if metric:
            scored = [
                (c, _score_match(metric, c["metric_name"], c["capability_name"], c["business_definition"], " ".join(c["triggers"])))
                for c in calibers
            ]
            max_sc = max((sc for _, sc in scored), default=0)
            cutoff = max(18, int(max_sc * 0.45)) if max_sc >= 20 else 1
            hits = [c for c, sc in sorted(scored, key=lambda x: x[1], reverse=True) if sc >= cutoff]
            if hits:
                calibers = hits
            elif not any(g in metric for g in ("全部", "所有", "列表")):
                calibers = []
        out = {
            "tool": tool_name,
            "calibers": [
                {
                    "code": c["code"],
                    "metric_name": sanitize_business_text(c["metric_name"]),
                    "capability_name": c["capability_name"],
                    "business_definition": sanitize_business_text(c["business_definition"]),
                    "formula_summary": sanitize_business_text(c["formula_summary"]),
                    "inclusion_scope": sanitize_business_text(c["inclusion_scope"]),
                    "exclusion_notes": sanitize_business_text(c["exclusion_notes"]),
                    "distinction_notes": sanitize_business_text(c["distinction_notes"]),
                    "reconcile_status": c["reconcile_status"],
                    "conflict_notes": sanitize_business_text(c["conflict_notes"]),
                    "status": c["status"],
                }
                for c in calibers
            ],
        }
        if not calibers:
            out.update(build_disclosure("not_found", metric))
        elif any(c["reconcile_status"] == "DIVERGENT" for c in calibers[:4]):
            names = "、".join(c["metric_name"] for c in calibers[:4] if c["reconcile_status"] == "DIVERGENT")
            out.update(build_disclosure("divergent", names))
        elif any(c["reconcile_status"] == "UNREALIZED" for c in calibers[:4]):
            names = "、".join(c["metric_name"] for c in calibers[:4] if c["reconcile_status"] == "UNREALIZED")
            out.update(build_disclosure("unrealized", names))
        return out

    if tool_name == "verify_implementation_backing":
        codes = args.get("asset_codes") or []
        rules_by_code = {r["code"]: r for r in list_business_rules(session)}
        cals_by_code = {c["code"]: c for c in list_business_calibers(session)}
        verdicts: List[Dict[str, Any]] = []
        for code in codes:
            item = rules_by_code.get(code) or cals_by_code.get(code)
            if not item:
                continue
            verdicts.append(
                {
                    "code": code,
                    "title": item.get("title") or item.get("metric_name"),
                    "reconcile_status": item["reconcile_status"],
                    "logic_backing_count": item["logic_backing_count"],
                    "clause_backing_count": item["clause_backing_count"],
                    "backing_verdict": (
                        f"已获 {item['logic_backing_count']} 处系统底层逻辑链路与 {item['clause_backing_count']} 条制度条款交叉实证"
                        if item["logic_backing_count"] > 0
                        else "仅制度材料提及，底层系统逻辑中尚无实现支撑"
                    ),
                }
            )
        out = {"tool": tool_name, "verdicts": verdicts}
        if any(v["reconcile_status"] == "UNREALIZED" for v in verdicts):
            out.update(build_disclosure("unrealized"))
        elif any(v["reconcile_status"] == "DIVERGENT" for v in verdicts):
            out.update(build_disclosure("divergent"))
        return out

    if tool_name == "check_regulatory_coverage_gap":
        ledger = list_reconcile_ledger(session)
        divergent = [x for x in ledger["items"] if x["reconcile_status"] == "DIVERGENT"]
        unrealized = [x for x in ledger["items"] if x["reconcile_status"] == "UNREALIZED"]
        verified = [x for x in ledger["items"] if x["reconcile_status"] in {"VERIFIED", "CONSISTENT"}]
        out = {
            "tool": tool_name,
            "summary": ledger["summary"],
            "verified_items": verified,
            "divergent_items": divergent,
            "unrealized_items": unrealized,
        }
        if unrealized:
            out.update(build_disclosure("unrealized", "、".join(x["name"] for x in unrealized)))
        elif divergent:
            out.update(build_disclosure("divergent", "、".join(x["name"] for x in divergent)))
        return out

    if tool_name == "find_rules_by_business_role":
        role = str(args.get("role") or args.get("audience") or "业务").strip()
        rules = [r for r in list_business_rules(session) if role in r["audience"]]
        cals = [c for c in list_business_calibers(session) if role in c["audience"]]
        out = {"tool": tool_name, "role": role, "rules": rules, "calibers": cals}
        if not (rules or cals):
            out.update(build_disclosure("not_found", role))
        return out

    return {"tool": tool_name, "error": f"Unknown business tool: {tool_name}"}
