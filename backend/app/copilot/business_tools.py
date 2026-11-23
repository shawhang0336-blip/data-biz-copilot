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
from app.copilot.disclosure import build_disclosure, format_disclosure_subjects
from app.infra.retrieval import bm25_score_documents, infer_regulatory_regime
from app.infra.taxonomy import detect_query_financial_themes, normalize_financial_theme
from app.policy.dc_bus import sanitize_business_text

BUSINESS_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "list_covered_businesses",
        "description": "盘点系统逻辑里已经包含的全部业务板块（含所属监管体系 1104/EAST/SAFE/PLUS、二级子专题与金融主题分布）、规则数量与覆盖落地状态。",
    },
    {
        "name": "resolve_business_concept",
        "description": "把业务人员的日常问法映射到对应的监管业务板块、二级子专题、金融十大主题或指标口径卡。",
    },
    {
        "name": "get_business_scope_and_rules",
        "description": "查询某项业务、二级子专题（sub_domain）或金融十大主题（subject_theme，支持跨报表横向对比）下的纳入、剔除、分支与防重规则。",
    },
    {
        "name": "get_business_caliber_detail",
        "description": "查询某个业务指标（如同业存放、贷款五级分类、交易金额、交易后余额等，支持传 subject_theme 跨体系过滤）的定义、算法口径与防混淆注意事项。",
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


def _rule_intent_bonus(topic: str, rule: Dict[str, Any]) -> int:
    bonus = 0
    if any(k in topic for k in ("剔除", "排除", "不报", "跳过", "风险清单", "异常")):
        if rule.get("rule_category") == "EXCLUSION":
            bonus += 12
    if any(k in topic for k in ("分支", "差异", "不一致", "分歧", "并行", "冲突")):
        if rule.get("rule_category") == "BRANCH" or rule.get("reconcile_status") == "DIVERGENT":
            bonus += 12
    if any(k in topic for k in ("去重", "防重", "重复", "唯一")):
        if rule.get("rule_category") == "DEDUP":
            bonus += 12
    if any(k in topic for k in ("纳入", "范围", "门槛", "覆盖")):
        if rule.get("rule_category") == "INCLUSION":
            bonus += 6
    return bonus


def _rank_by_bm25(
    query: str,
    items: List[Dict[str, Any]],
    *,
    field_weights: Dict[str, float],
    extractor,
    min_ratio: float = 0.35,
    min_floor: float = 0.25,
) -> List[Dict[str, Any]]:
    if not items or not (query or "").strip():
        return items
    docs = [extractor(item) for item in items]
    regimes = [str(item.get("regime") or "通用监管") for item in items]
    scores = bm25_score_documents(query, docs, field_weights=field_weights, regimes=regimes)
    max_sc = max(scores, default=0.0)
    if max_sc <= 0.0:
        return []
    cutoff = max(min_floor, max_sc * min_ratio)
    ranked = [item for item, sc in sorted(zip(items, scores), key=lambda x: x[1], reverse=True) if sc >= cutoff]
    return ranked


def _diversify_by_capability(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """跨业务域/体系轮转挑选 Top-K，避免单一报表独占全部横切查询配额。"""
    if len(items) <= limit:
        return items
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for item in items:
        cap = str(item.get("capability_name") or "")
        if cap not in buckets:
            order.append(cap)
            buckets[cap] = []
        buckets[cap].append(item)
    picked: List[Dict[str, Any]] = []
    round_idx = 0
    while len(picked) < limit:
        added = False
        for cap in order:
            if round_idx < len(buckets[cap]):
                picked.append(buckets[cap][round_idx])
                added = True
                if len(picked) >= limit:
                    break
        if not added:
            break
        round_idx += 1
    return picked


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
                    "regime": c.get("regime", "通用监管"),
                    "sub_domains": [s["name"] for s in (c.get("sub_domains") or [])[:6]],
                    "subject_themes": c.get("subject_themes") or [],
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
                    "regime": c.get("regime", "通用监管"),
                    "sub_domains": [s["name"] for s in (c.get("sub_domains") or [])[:6]],
                    "subject_themes": c.get("subject_themes") or [],
                    "description": sanitize_business_text(c["description"]),
                    "coverage_status": c["coverage_status"],
                }
                for c in unrealized
            ],
        }
        if not caps:
            out.update(build_disclosure("not_found"))
        elif unrealized:
            out.update(build_disclosure("unrealized", format_disclosure_subjects(c["name"] for c in unrealized)))
        return out

    if tool_name == "resolve_business_concept":
        query = str(args.get("concept") or args.get("query") or "")
        caps = list_capabilities(session)
        rules = list_business_rules(session)
        calibers = list_business_calibers(session)
        matched_caps = _rank_by_bm25(
            query,
            caps,
            field_weights={"name": 2.6, "regime": 1.6, "sub_domains": 1.6, "subject_themes": 1.4, "description": 1.2, "scope_summary": 1.0},
            extractor=lambda c: {
                "name": c["name"],
                "regime": c.get("regime", "通用监管"),
                "sub_domains": " ".join(s["name"] for s in (c.get("sub_domains") or [])),
                "subject_themes": " ".join(c.get("subject_themes") or []),
                "description": c["description"],
                "scope_summary": c["scope_summary"],
            },
        )[:4]
        matched_rules = _rank_by_bm25(
            query,
            rules,
            field_weights={"title": 2.4, "sub_domain": 1.8, "subject_theme": 1.6, "capability_name": 1.5, "regime": 1.4, "business_condition": 1.1, "business_action": 1.0},
            extractor=lambda r: {
                "title": r["title"],
                "sub_domain": r.get("sub_domain", ""),
                "subject_theme": r.get("subject_theme", ""),
                "capability_name": r["capability_name"],
                "regime": r.get("regime", "通用监管"),
                "business_condition": r["business_condition"],
                "business_action": r["business_action"],
            },
        )[:5]
        matched_cals = _rank_by_bm25(
            query,
            calibers,
            field_weights={"metric_name": 2.8, "triggers": 2.0, "sub_domain": 1.7, "subject_theme": 1.5, "regime": 1.4, "business_definition": 1.0},
            extractor=lambda c: {
                "metric_name": c["metric_name"],
                "triggers": " ".join(c["triggers"]),
                "sub_domain": c.get("sub_domain", ""),
                "subject_theme": c.get("subject_theme", ""),
                "regime": c.get("regime", "通用监管"),
                "business_definition": c["business_definition"],
            },
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
        explicit_sub_domain = str(args.get("sub_domain") or "").strip()
        explicit_theme = normalize_financial_theme(str(args.get("subject_theme") or ""))
        detected_themes = detect_query_financial_themes(topic) if topic else []
        target_themes = [explicit_theme] if explicit_theme else detected_themes

        rules = list_business_rules(
            session,
            rule_category=category,
            sub_domain=explicit_sub_domain or None,
        )
        theme_scoped = False
        if target_themes:
            theme_hits = [r for r in rules if r.get("subject_theme") in set(target_themes)]
            if theme_hits or explicit_theme:
                rules = theme_hits
                theme_scoped = True

        total_matched = len(rules)
        if topic and rules:
            docs = [
                {
                    "title": r["title"],
                    "capability_name": r["capability_name"],
                    "regime": r.get("regime", "通用监管"),
                    "sub_domain": r.get("sub_domain", ""),
                    "subject_theme": r.get("subject_theme", ""),
                    "business_condition": r["business_condition"],
                    "business_action": r["business_action"],
                }
                for r in rules
            ]
            regimes = [str(r.get("regime") or "通用监管") for r in rules]
            base_scores = bm25_score_documents(
                topic,
                docs,
                field_weights={
                    "title": 2.5,
                    "sub_domain": 1.8,
                    "subject_theme": 1.7,
                    "capability_name": 1.6,
                    "regime": 1.5,
                    "business_condition": 1.2,
                    "business_action": 1.0,
                },
                regimes=regimes,
            )
            max_base = max(base_scores, default=0.0)
            cutoff = max(0.25, max_base * 0.42) if max_base > 0.0 else 0.25
            scored = [
                (r, bsc, (bsc + _rule_intent_bonus(topic, r)) if bsc > 0.0 else _rule_intent_bonus(topic, r))
                for r, bsc in zip(rules, base_scores)
            ]
            if theme_scoped or explicit_sub_domain:
                ordered = [r for r, _bsc, _comb in sorted(scored, key=lambda x: (x[2], x[1]), reverse=True)]
                total_matched = len(ordered)
                rules = _diversify_by_capability(ordered, 15) if theme_scoped else ordered[:15]
            else:
                filtered = [r for r, bsc, _ in sorted(scored, key=lambda x: (x[2], x[1]), reverse=True) if bsc >= cutoff]
                if filtered:
                    total_matched = len(filtered)
                    rules = filtered[:15]
                elif not any(g in topic for g in ("全部", "所有", "列表")):
                    total_matched = 0
                    rules = []
                else:
                    rules = rules[:15]
        elif not topic:
            rules = _diversify_by_capability(rules, 15) if theme_scoped else rules[:15]

        out = {
            "tool": tool_name,
            "total_matched": total_matched,
            "applied_filters": {
                "subject_themes": target_themes,
                "sub_domain": explicit_sub_domain,
            },
            "rules": [
                {
                    "code": r["code"],
                    "title": sanitize_business_text(r["title"]),
                    "capability_name": r["capability_name"],
                    "regime": r.get("regime", "通用监管"),
                    "sub_domain": r.get("sub_domain", ""),
                    "subject_theme": r.get("subject_theme", ""),
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
            out.update(build_disclosure("not_found", explicit_theme or topic))
        elif any(r["reconcile_status"] == "UNREALIZED" for r in rules[:6]):
            unreal_names = format_disclosure_subjects(r["title"] for r in rules[:6] if r["reconcile_status"] == "UNREALIZED")
            out.update(build_disclosure("unrealized", unreal_names or "部分待落地规则"))
        elif any(r["reconcile_status"] == "DIVERGENT" for r in rules[:6]):
            div_names = format_disclosure_subjects(r["title"] for r in rules[:6] if r["reconcile_status"] == "DIVERGENT")
            out.update(build_disclosure("divergent", div_names or "分支处理口径分歧"))
        return out

    if tool_name == "get_business_caliber_detail":
        metric = str(args.get("metric") or args.get("query") or "").strip()
        explicit_sub_domain = str(args.get("sub_domain") or "").strip()
        explicit_theme = normalize_financial_theme(str(args.get("subject_theme") or ""))
        detected_themes = detect_query_financial_themes(metric) if metric else []
        target_themes = [explicit_theme] if explicit_theme else detected_themes

        calibers = list_business_calibers(session, sub_domain=explicit_sub_domain or None)
        theme_scoped = False
        if target_themes:
            theme_hits = [c for c in calibers if c.get("subject_theme") in set(target_themes)]
            if theme_hits or explicit_theme:
                calibers = theme_hits
                theme_scoped = True

        total_matched = len(calibers)
        if metric and calibers:
            hits = _rank_by_bm25(
                metric,
                calibers,
                field_weights={
                    "metric_name": 2.8,
                    "triggers": 2.2,
                    "sub_domain": 1.8,
                    "subject_theme": 1.7,
                    "capability_name": 1.6,
                    "regime": 1.5,
                    "business_definition": 1.0,
                },
                extractor=lambda c: {
                    "metric_name": c["metric_name"],
                    "triggers": " ".join(c["triggers"]),
                    "sub_domain": c.get("sub_domain", ""),
                    "subject_theme": c.get("subject_theme", ""),
                    "capability_name": c["capability_name"],
                    "regime": c.get("regime", "通用监管"),
                    "business_definition": c["business_definition"],
                },
            )
            if hits:
                total_matched = len(hits)
                calibers = hits[:8]
            elif theme_scoped or explicit_sub_domain:
                calibers = calibers[:8]
            elif not any(g in metric for g in ("全部", "所有", "列表")):
                total_matched = 0
                calibers = []
            else:
                calibers = calibers[:8]
        elif not metric:
            calibers = calibers[:8]
        out = {
            "tool": tool_name,
            "total_matched": total_matched,
            "calibers": [
                {
                    "code": c["code"],
                    "metric_name": sanitize_business_text(c["metric_name"]),
                    "capability_name": c["capability_name"],
                    "regime": c.get("regime", "通用监管"),
                    "sub_domain": c.get("sub_domain", ""),
                    "subject_theme": c.get("subject_theme", ""),
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
            out.update(build_disclosure("not_found", explicit_theme or metric))
        elif any(c["reconcile_status"] == "DIVERGENT" for c in calibers[:4]):
            names = format_disclosure_subjects(c["metric_name"] for c in calibers[:4] if c["reconcile_status"] == "DIVERGENT")
            out.update(build_disclosure("divergent", names))
        elif any(c["reconcile_status"] == "UNREALIZED" for c in calibers[:4]):
            names = format_disclosure_subjects(c["metric_name"] for c in calibers[:4] if c["reconcile_status"] == "UNREALIZED")
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
        topic = str(args.get("topic") or args.get("query") or "").strip()
        ledger = list_reconcile_ledger(session)
        divergent = [x for x in ledger["items"] if x["reconcile_status"] == "DIVERGENT"]
        unrealized = [x for x in ledger["items"] if x["reconcile_status"] == "UNREALIZED"]
        verified = [x for x in ledger["items"] if x["reconcile_status"] in {"VERIFIED", "CONSISTENT"}]
        if topic:
            extractor = lambda x: {
                "name": x["name"],
                "capability_name": x["capability_name"],
                "regime": x.get("regime", infer_regulatory_regime(x["capability_name"], x["name"])),
                "sub_domain": x.get("sub_domain", ""),
                "subject_theme": x.get("subject_theme", ""),
                "summary": x["summary"],
            }
            fw = {"name": 2.5, "sub_domain": 1.7, "subject_theme": 1.6, "capability_name": 1.6, "regime": 1.4, "summary": 1.0}
            divergent = _rank_by_bm25(topic, divergent, field_weights=fw, extractor=extractor)
            unrealized = _rank_by_bm25(topic, unrealized, field_weights=fw, extractor=extractor)
            verified = _rank_by_bm25(topic, verified, field_weights=fw, extractor=extractor)[:15]
        else:
            verified = verified[:15]
        out = {
            "tool": tool_name,
            "topic": topic,
            "summary": ledger["summary"],
            "verified_items": verified,
            "divergent_items": divergent[:15],
            "unrealized_items": unrealized[:15],
        }
        prefers_divergent = bool(topic) and any(k in topic for k in ("分歧", "不一致", "冲突", "并行", "口径"))
        if prefers_divergent and divergent:
            out.update(build_disclosure("divergent", format_disclosure_subjects(x["name"] for x in divergent)))
        elif unrealized:
            out.update(build_disclosure("unrealized", format_disclosure_subjects(x["name"] for x in unrealized)))
        elif divergent:
            out.update(build_disclosure("divergent", format_disclosure_subjects(x["name"] for x in divergent)))
        return out

    if tool_name == "find_rules_by_business_role":
        role = str(args.get("role") or args.get("audience") or "业务").strip()
        all_rules = [r for r in list_business_rules(session) if role in r["audience"]]
        all_cals = [c for c in list_business_calibers(session) if role in c["audience"]]
        out = {
            "tool": tool_name,
            "role": role,
            "total_matched_rules": len(all_rules),
            "total_matched_calibers": len(all_cals),
            "rules": all_rules[:15],
            "calibers": all_cals[:8],
        }
        if not (all_rules or all_cals):
            out.update(build_disclosure("not_found", role))
        return out

    return {"tool": tool_name, "error": f"Unknown business tool: {tool_name}"}
