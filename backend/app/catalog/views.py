"""纯业务资产层只读视图查询（资产层 Layer 4）。

严守隐私与去技术化边界：任何返回前端或问答层的视图对象均不输出 `LogicEvidenceModel.internal_locator_json`
中的物理表名、字段名或 SQL 语句，只呈现业务表述与实证支撑状态。
"""

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.infra.db_tables import (
    AssetRevisionModel,
    BusinessCaliberAssetModel,
    BusinessCapabilityModel,
    BusinessRuleAssetModel,
    LogicEvidenceModel,
    RegulatoryClauseModel,
    SourceMaterialModel,
)
from app.infra.retrieval import (
    agency_for_regime,
    infer_regulatory_regime,
    invalidate_bm25_caches,
    matches_agency_or_regime,
    rule_studio_regulator_code,
)
from app.infra.taxonomy import (
    FINANCIAL_SUBJECT_THEMES,
    infer_financial_theme,
    infer_sub_domain,
    normalize_financial_theme,
    summarize_subject_theme_matrix,
)
from app.policy.dc_bus import sanitize_business_text


def _compute_capability_regimes(
    session: Session, caps: List[BusinessCapabilityModel]
) -> Dict[str, str]:
    """根据关联条款/底层实证来源及业务概述自动归纳所属监管报送体系（1104 / EAST / SAFE / PLUS / 一表通 / 人行统计）。"""
    clause_regimes = {
        cid: (loc or {}).get("regime")
        for cid, loc in session.query(RegulatoryClauseModel.id, RegulatoryClauseModel.source_locator_json).all()
        if isinstance(loc, dict) and loc.get("regime")
    }
    ev_regimes = {
        eid: (loc or {}).get("regime")
        for eid, loc in session.query(LogicEvidenceModel.id, LogicEvidenceModel.internal_locator_json).all()
        if isinstance(loc, dict) and loc.get("regime")
    }
    result: Dict[str, str] = {}
    for cap in caps:
        counts: Dict[str, int] = {}
        for cid in cap.clause_ids_json or []:
            reg = clause_regimes.get(cid)
            if reg and reg != "通用监管":
                counts[reg] = counts.get(reg, 0) + 1
        for eid in cap.evidence_ids_json or []:
            reg = ev_regimes.get(eid)
            if reg and reg != "通用监管":
                counts[reg] = counts.get(reg, 0) + 1
        if counts:
            result[cap.id] = max(counts.items(), key=lambda kv: kv[1])[0]
        else:
            result[cap.id] = infer_regulatory_regime(cap.name, cap.description, cap.scope_summary)
    return result


def _rule_to_dict(
    row: BusinessRuleAssetModel,
    capability_name: str = "",
    regime: str = "通用监管",
) -> Dict[str, Any]:
    sub_domain = (getattr(row, "sub_domain", None) or "").strip() or infer_sub_domain(
        capability_name,
        row.title,
        row.business_condition,
        row.business_action,
        rule_category=row.rule_category,
    )
    raw_theme = getattr(row, "subject_theme", None) or ""
    subject_theme = normalize_financial_theme(raw_theme) or infer_financial_theme(
        row.title,
        row.business_condition,
        row.business_action,
        capability_name,
        row.rule_category,
    )
    return {
        "id": row.id,
        "capability_id": row.capability_id,
        "capability_name": capability_name,
        "regime": regime,
        "agency": agency_for_regime(regime),
        "sub_domain": sub_domain,
        "subject_theme": subject_theme,
        "code": row.code,
        "title": row.title,
        "rule_category": row.rule_category,
        "business_condition": row.business_condition,
        "business_action": row.business_action,
        "business_rationale": row.business_rationale,
        "reconcile_status": row.reconcile_status,
        "logic_backing_count": len(row.evidence_ids_json or []),
        "clause_backing_count": len(row.clause_ids_json or []),
        "audience": row.audience,
        "status": row.status,
        "current_version": row.current_version,
        "confirmed_by": row.confirmed_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _caliber_to_dict(
    row: BusinessCaliberAssetModel,
    capability_name: str = "",
    regime: str = "通用监管",
) -> Dict[str, Any]:
    sub_domain = (getattr(row, "sub_domain", None) or "").strip() or infer_sub_domain(
        capability_name,
        row.metric_name,
        row.business_definition,
        row.formula_summary,
        rule_category="CALIBER",
    )
    raw_theme = getattr(row, "subject_theme", None) or ""
    subject_theme = normalize_financial_theme(raw_theme) or infer_financial_theme(
        row.metric_name,
        row.business_definition,
        row.formula_summary,
        capability_name,
        "CALIBER",
    )
    return {
        "id": row.id,
        "capability_id": row.capability_id,
        "capability_name": capability_name,
        "regime": regime,
        "agency": agency_for_regime(regime),
        "sub_domain": sub_domain,
        "subject_theme": subject_theme,
        "code": row.code,
        "metric_name": row.metric_name,
        "business_definition": row.business_definition,
        "formula_summary": row.formula_summary,
        "inclusion_scope": row.inclusion_scope,
        "exclusion_notes": row.exclusion_notes,
        "distinction_notes": row.distinction_notes,
        "triggers": list(row.triggers_json or []),
        "reconcile_status": row.reconcile_status,
        "conflict_notes": row.conflict_notes,
        "logic_backing_count": len(row.evidence_ids_json or []),
        "clause_backing_count": len(row.clause_ids_json or []),
        "audience": row.audience,
        "status": row.status,
        "current_version": row.current_version,
        "confirmed_by": row.confirmed_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_catalog_cache_signature(session: Session, kind: str) -> Tuple[Any, ...]:
    """返回轻量级资产版本号签名（行数 + 最近更新时间），用于安全驱动内存 BM25 缓存。"""
    bind_id = id(session.get_bind())
    if kind == "cap":
        cnt, max_ts = session.query(
            func.count(BusinessCapabilityModel.id),
            func.max(BusinessCapabilityModel.updated_at),
        ).one()
    elif kind == "rule":
        cnt, max_ts = session.query(
            func.count(BusinessRuleAssetModel.id),
            func.max(BusinessRuleAssetModel.updated_at),
        ).one()
    elif kind == "caliber":
        cnt, max_ts = session.query(
            func.count(BusinessCaliberAssetModel.id),
            func.max(BusinessCaliberAssetModel.updated_at),
        ).one()
    else:
        return (bind_id, kind, 0, "")
    return (bind_id, kind, int(cnt or 0), max_ts.isoformat() if max_ts else "")


def list_source_materials(session: Session) -> List[Dict[str, Any]]:
    rows = session.query(SourceMaterialModel).order_by(SourceMaterialModel.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "material_kind": r.material_kind,
            "file_type": r.file_type,
            "status": r.status,
            "tier": getattr(r, "tier", "TIER_1_CORE") or "TIER_1_CORE",
            "skip_reason": getattr(r, "skip_reason", "") or "",
            "batch_job_id": getattr(r, "batch_job_id", None),
            "relative_path": getattr(r, "relative_path", "") or r.title,
            "summary": r.summary,
            "item_count": r.item_count,
            "error_message": getattr(r, "error_message", "") or "",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def _compute_business_lineage(session: Session) -> Dict[str, Dict[str, List[str]]]:
    """依据底层实证读写表拓扑计算纯业务层面的上下游业务板块流向（绝不向外返回任何表名）。"""
    evidences = session.query(LogicEvidenceModel).all()
    writers: Dict[str, List[str]] = {}
    readers: Dict[str, List[str]] = {}
    for ev in evidences:
        loc = ev.internal_locator_json or {}
        dom = ev.business_domain_hint
        tgt_tbls = [str(t).upper() for t in (loc.get("target_tables") or []) if t]
        src_tbls = [str(t).upper() for t in (loc.get("source_tables") or []) if t]
        if not tgt_tbls and not src_tbls:
            objs = [str(t).upper() for t in (loc.get("physical_objects") or []) if t]
            if objs:
                tgt_tbls = [objs[0]]
                src_tbls = objs[1:]
        for t in tgt_tbls:
            writers.setdefault(t, [])
            if dom not in writers[t]:
                writers[t].append(dom)
        for t in src_tbls:
            readers.setdefault(t, [])
            if dom not in readers[t]:
                readers[t].append(dom)

    upstream: Dict[str, List[str]] = {}
    downstream: Dict[str, List[str]] = {}
    for tbl, w_domains in writers.items():
        r_domains = readers.get(tbl, [])
        for w in w_domains:
            for r in r_domains:
                if w == r:
                    continue
                downstream.setdefault(w, [])
                if r not in downstream[w]:
                    downstream[w].append(r)
                upstream.setdefault(r, [])
                if w not in upstream[r]:
                    upstream[r].append(w)
    return {"upstream": upstream, "downstream": downstream}


def list_capabilities(
    session: Session,
    *,
    agency: Optional[str] = None,
    regime: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows = session.query(BusinessCapabilityModel).order_by(BusinessCapabilityModel.name.asc()).all()
    lineage = _compute_business_lineage(session)
    regimes = _compute_capability_regimes(session, rows)
    cap_names = {r.id: r.name for r in rows}

    sub_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
    theme_counts: Dict[str, Dict[str, int]] = {}
    for rule in session.query(BusinessRuleAssetModel).all():
        cname = cap_names.get(rule.capability_id, "")
        sd = (getattr(rule, "sub_domain", None) or "").strip() or infer_sub_domain(
            cname, rule.title, rule.business_condition, rule.business_action, rule_category=rule.rule_category
        )
        th = normalize_financial_theme(getattr(rule, "subject_theme", None) or "") or infer_financial_theme(
            rule.title, rule.business_condition, rule.business_action, cname, rule.rule_category
        )
        sub_entry = sub_stats.setdefault(rule.capability_id, {}).setdefault(sd, {"rule_count": 0, "caliber_count": 0})
        sub_entry["rule_count"] += 1
        th_map = theme_counts.setdefault(rule.capability_id, {})
        th_map[th] = th_map.get(th, 0) + 1

    for cal in session.query(BusinessCaliberAssetModel).all():
        cname = cap_names.get(cal.capability_id, "")
        sd = (getattr(cal, "sub_domain", None) or "").strip() or infer_sub_domain(
            cname, cal.metric_name, cal.business_definition, cal.formula_summary, rule_category="CALIBER"
        )
        th = normalize_financial_theme(getattr(cal, "subject_theme", None) or "") or infer_financial_theme(
            cal.metric_name, cal.business_definition, cal.formula_summary, cname, "CALIBER"
        )
        sub_entry = sub_stats.setdefault(cal.capability_id, {}).setdefault(sd, {"rule_count": 0, "caliber_count": 0})
        sub_entry["caliber_count"] += 1
        th_map = theme_counts.setdefault(cal.capability_id, {})
        th_map[th] = th_map.get(th, 0) + 1

    out: List[Dict[str, Any]] = []
    for r in rows:
        s_map = sub_stats.get(r.id, {})
        sorted_subs = [
            {"name": name, "rule_count": cnts["rule_count"], "caliber_count": cnts["caliber_count"]}
            for name, cnts in sorted(
                s_map.items(),
                key=lambda kv: (kv[1]["rule_count"] + kv[1]["caliber_count"], kv[0]),
                reverse=True,
            )
        ]
        t_map = theme_counts.get(r.id, {})
        sorted_themes = [
            t for t, _ in sorted(t_map.items(), key=lambda kv: kv[1], reverse=True)
        ]
        out.append(
            {
                "id": r.id,
                "code": r.code,
                "name": r.name,
                "regime": regimes.get(r.id, "通用监管"),
                "sub_domains": sorted_subs,
                "subject_themes": sorted_themes,
                "description": r.description,
                "scope_summary": r.scope_summary,
                "audience": r.audience,
                "coverage_status": r.coverage_status,
                "rule_count": r.rule_count,
                "caliber_count": r.caliber_count,
                "confirmed_count": r.confirmed_count,
                "logic_backing_count": len(r.evidence_ids_json or []),
                "clause_backing_count": len(r.clause_ids_json or []),
                "upstream_businesses": lineage["upstream"].get(r.name, []),
                "downstream_businesses": lineage["downstream"].get(r.name, []),
                "status": r.status,
                "current_version": r.current_version,
                "agency": agency_for_regime(regimes.get(r.id, "通用监管")),
            }
        )
    if agency or regime:
        out = [x for x in out if matches_agency_or_regime(x.get("regime"), agency=agency, regime=regime)]
    return out


def list_business_rules(
    session: Session,
    *,
    capability_id: Optional[str] = None,
    rule_category: Optional[str] = None,
    reconcile_status: Optional[str] = None,
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    sub_domain: Optional[str] = None,
    subject_theme: Optional[str] = None,
) -> List[Dict[str, Any]]:
    cap_rows = session.query(BusinessCapabilityModel).all()
    caps = {c.id: c.name for c in cap_rows}
    regimes = _compute_capability_regimes(session, cap_rows)
    q = session.query(BusinessRuleAssetModel)
    if capability_id:
        q = q.filter(BusinessRuleAssetModel.capability_id == capability_id)
    if rule_category:
        q = q.filter(BusinessRuleAssetModel.rule_category == rule_category)
    if reconcile_status:
        q = q.filter(BusinessRuleAssetModel.reconcile_status == reconcile_status)
    rows = q.order_by(BusinessRuleAssetModel.code.asc()).all()
    items = [_rule_to_dict(r, caps.get(r.capability_id, ""), regimes.get(r.capability_id, "通用监管")) for r in rows]
    norm_theme = normalize_financial_theme(subject_theme or "") if subject_theme else ""
    if agency or regime:
        items = [x for x in items if matches_agency_or_regime(x["regime"], agency=agency, regime=regime)]
    if sub_domain:
        items = [x for x in items if x["sub_domain"] == sub_domain]
    if subject_theme:
        target_theme = norm_theme or subject_theme
        items = [x for x in items if x["subject_theme"] == target_theme]
    return items


def list_business_calibers(
    session: Session,
    *,
    capability_id: Optional[str] = None,
    reconcile_status: Optional[str] = None,
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    sub_domain: Optional[str] = None,
    subject_theme: Optional[str] = None,
) -> List[Dict[str, Any]]:
    cap_rows = session.query(BusinessCapabilityModel).all()
    caps = {c.id: c.name for c in cap_rows}
    regimes = _compute_capability_regimes(session, cap_rows)
    q = session.query(BusinessCaliberAssetModel)
    if capability_id:
        q = q.filter(BusinessCaliberAssetModel.capability_id == capability_id)
    if reconcile_status:
        q = q.filter(BusinessCaliberAssetModel.reconcile_status == reconcile_status)
    rows = q.order_by(BusinessCaliberAssetModel.code.asc()).all()
    items = [_caliber_to_dict(r, caps.get(r.capability_id, ""), regimes.get(r.capability_id, "通用监管")) for r in rows]
    norm_theme = normalize_financial_theme(subject_theme or "") if subject_theme else ""
    if agency or regime:
        items = [x for x in items if matches_agency_or_regime(x["regime"], agency=agency, regime=regime)]
    if sub_domain:
        items = [x for x in items if x["sub_domain"] == sub_domain]
    if subject_theme:
        target_theme = norm_theme or subject_theme
        items = [x for x in items if x["subject_theme"] == target_theme]
    return items


def list_reconcile_ledger(
    session: Session,
    *,
    agency: Optional[str] = None,
    regime: Optional[str] = None,
) -> Dict[str, Any]:
    rules = list_business_rules(session, agency=agency, regime=regime)
    calibers = list_business_calibers(session, agency=agency, regime=regime)
    summary = {"VERIFIED": 0, "CONSISTENT": 0, "DIVERGENT": 0, "UNREALIZED": 0}
    for item in rules + calibers:
        st = item["reconcile_status"]
        summary[st] = summary.get(st, 0) + 1
    return {
        "summary": summary,
        "items": [
            {
                "asset_kind": "CALIBER",
                "id": c["id"],
                "code": c["code"],
                "name": c["metric_name"],
                "capability_name": c["capability_name"],
                "regime": c.get("regime", "通用监管"),
                "agency": c.get("agency", agency_for_regime(c.get("regime"))),
                "sub_domain": c.get("sub_domain", ""),
                "subject_theme": c.get("subject_theme", ""),
                "reconcile_status": c["reconcile_status"],
                "status": c["status"],
                "current_version": c["current_version"],
                "summary": c["conflict_notes"] or c["formula_summary"],
                "logic_backing_count": c["logic_backing_count"],
                "clause_backing_count": c["clause_backing_count"],
            }
            for c in calibers
        ]
        + [
            {
                "asset_kind": "RULE",
                "id": r["id"],
                "code": r["code"],
                "name": r["title"],
                "capability_name": r["capability_name"],
                "regime": r.get("regime", "通用监管"),
                "agency": r.get("agency", agency_for_regime(r.get("regime"))),
                "sub_domain": r.get("sub_domain", ""),
                "subject_theme": r.get("subject_theme", ""),
                "reconcile_status": r["reconcile_status"],
                "status": r["status"],
                "current_version": r["current_version"],
                "summary": r["business_action"],
                "logic_backing_count": r["logic_backing_count"],
                "clause_backing_count": r["clause_backing_count"],
            }
            for r in rules
        ],
    }


def get_asset_panorama(session: Session) -> Dict[str, Any]:
    capabilities = list_capabilities(session)
    rules = list_business_rules(session)
    calibers = list_business_calibers(session)
    ledger = list_reconcile_ledger(session)
    materials = list_source_materials(session)

    regime_stats: Dict[str, Dict[str, int]] = {}
    for c in capabilities:
        reg = c.get("regime") or "通用监管"
        entry = regime_stats.setdefault(
            reg, {"regime": reg, "capability_count": 0, "covered_capabilities": 0, "rule_count": 0, "caliber_count": 0}
        )
        entry["capability_count"] += 1
        if c["coverage_status"] in {"COVERED", "PARTIAL"}:
            entry["covered_capabilities"] += 1
        entry["rule_count"] += int(c.get("rule_count") or 0)
        entry["caliber_count"] += int(c.get("caliber_count") or 0)

    return {
        "materials": materials,
        "capabilities": capabilities,
        "rules": rules,
        "calibers": calibers,
        "reconcile": ledger,
        "taxonomy": {
            "regimes": sorted(
                regime_stats.values(),
                key=lambda x: (x["rule_count"] + x["caliber_count"], x["capability_count"]),
                reverse=True,
            ),
            "subject_themes": summarize_subject_theme_matrix(rules, calibers),
            "financial_subject_themes": list(FINANCIAL_SUBJECT_THEMES),
        },
        "stats": {
            "capability_count": len(capabilities),
            "covered_capabilities": sum(1 for c in capabilities if c["coverage_status"] == "COVERED"),
            "unrealized_capabilities": sum(1 for c in capabilities if c["coverage_status"] == "UNREALIZED"),
            "rule_count": len(rules),
            "caliber_count": len(calibers),
            "confirmed_count": sum(1 for x in rules + calibers if x["status"] == "CONFIRMED"),
        },
    }


def get_asset_evidence_ids_map(session: Session, asset_codes: List[str]) -> Dict[str, List[str]]:
    """批量查询业务资产编号关联的底层实证 ID 列表。"""
    clean_codes = [c for c in asset_codes if c]
    if not clean_codes:
        return {}
    out: Dict[str, List[str]] = {}
    for row in (
        session.query(BusinessRuleAssetModel.code, BusinessRuleAssetModel.evidence_ids_json)
        .filter(BusinessRuleAssetModel.code.in_(clean_codes))
        .all()
    ):
        out[row[0]] = list(row[1] or [])
    for row in (
        session.query(BusinessCaliberAssetModel.code, BusinessCaliberAssetModel.evidence_ids_json)
        .filter(BusinessCaliberAssetModel.code.in_(clean_codes))
        .all()
    ):
        out[row[0]] = list(row[1] or [])
    return out


def _clean_flow_items(items: Any, *, max_items: int = 3, is_branch: bool = False) -> List[str]:
    out: List[str] = []
    for item in items or []:
        if is_branch:
            if isinstance(item, dict):
                cond = sanitize_business_text(str(item.get("condition") or item.get("title") or "").strip())
                act = sanitize_business_text(str(item.get("action") or "").strip())
                text = f"{cond} → {act}" if cond and act else (act or cond)
            else:
                text = sanitize_business_text(str(item or "").strip())
        else:
            if isinstance(item, dict):
                text = sanitize_business_text(str(item.get("condition") or item.get("title") or "").strip())
            else:
                text = sanitize_business_text(str(item or "").strip())
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


_STAGE_ROLE_LABELS = {
    "SCOPE_FILTER": "圈定范围",
    "BRANCH_LOGIC": "分情况处理",
    "CALCULATION": "加工计算",
    "DEDUP_MERGE": "合并去重",
    "OUTPUT_REPORT": "汇总输出",
}
_FLOW_ORDINALS = ["一", "二", "三", "四", "五"]


def _evidence_richness(ev: LogicEvidenceModel) -> Tuple[int, bool]:
    stage_raw = str(ev.target_concept_hint or "")
    src_raw = str(ev.source_summary or "")
    calc_raw = str(ev.calculation_summary or "")
    inc = ev.inclusion_predicates_json or []
    exc = ev.exclusion_predicates_json or []
    br = ev.branch_cases_json or []
    dedup_raw = str(ev.dedup_grain_hint or "").strip()
    is_ddl = (
        "数据底座结构" in stage_raw or "台账结构定义" in src_raw or "台账登记结构支撑" in calc_raw
    ) and not (inc or exc or br or dedup_raw)
    score = 4 * len(br) + 3 * len(exc) + 2 * len(inc)
    if dedup_raw:
        score += 3
    if calc_raw and "无复杂指标口径改写" not in calc_raw and "台账登记结构支撑" not in calc_raw:
        score += 2
    if len(src_raw) > 40:
        score += 2
    if is_ddl:
        score -= 10
    return score, is_ddl


def _merge_unique(target: List[str], items: List[str], max_items: int = 3) -> None:
    for it in items:
        if it and it not in target and len(target) < max_items:
            target.append(it)


def summarize_processing_flow(
    session: Session,
    evidence_ids: List[str],
    *,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """按真实执行先后顺序提炼去技术化加工步骤：同一加工流程内按代码出现顺序排列，相邻同阶段合并。

    脚本名、块序号、行号仅用于内部排序分组，绝不对外返回，对外只给「加工流程一/二」等中性标签。
    """
    clean_ids = [eid for eid in (evidence_ids or []) if eid]
    if not clean_ids or limit <= 0:
        return []
    rows = session.query(LogicEvidenceModel).filter(LogicEvidenceModel.id.in_(clean_ids)).all()
    if not rows:
        return []

    scored = {ev.id: _evidence_richness(ev) for ev in rows}
    has_real = any(not scored[ev.id][1] for ev in rows)
    usable = [ev for ev in rows if not (has_real and scored[ev.id][1])]

    groups: Dict[str, List[LogicEvidenceModel]] = {}
    for ev in usable:
        loc = ev.internal_locator_json or {}
        key = str(loc.get("script") or ev.material_id or "")
        groups.setdefault(key, []).append(ev)

    def _order_key(ev: LogicEvidenceModel) -> Tuple[int, int]:
        loc = ev.internal_locator_json or {}
        try:
            ci = int(loc.get("chunk_index"))
        except (TypeError, ValueError):
            ci = 10**6
        try:
            sl = int(loc.get("start_line"))
        except (TypeError, ValueError):
            sl = 10**6
        return ci, sl

    flows: List[Tuple[int, List[Dict[str, Any]]]] = []
    for _key, evs in groups.items():
        evs_sorted = sorted(evs, key=_order_key)
        stages: List[Dict[str, Any]] = []
        last_title = None
        for ev in evs_sorted:
            loc = ev.internal_locator_json or {}
            title_raw = str(loc.get("stage_title") or ev.target_concept_hint or ev.business_domain_hint or "").strip()
            stage = sanitize_business_text(title_raw) or "数据处理环节"
            flow_summary = sanitize_business_text(str(ev.source_summary or "").strip())
            calc_raw = str(ev.calculation_summary or "").strip()
            calc_text = "" if ("无复杂指标口径改写" in calc_raw or "台账登记结构支撑" in calc_raw) else sanitize_business_text(calc_raw)
            dedup_text = sanitize_business_text(str(ev.dedup_grain_hint or "").strip())
            inclusions = _clean_flow_items(ev.inclusion_predicates_json, max_items=3)
            exclusions = _clean_flow_items(ev.exclusion_predicates_json, max_items=3)
            branches = _clean_flow_items(ev.branch_cases_json, max_items=3, is_branch=True)
            if not (flow_summary or inclusions or exclusions or branches or calc_text or dedup_text):
                continue
            role = str(loc.get("stage_role") or "")
            if stages and last_title == stage:
                cur = stages[-1]
                if len(flow_summary) > len(cur["flow_summary"]):
                    cur["flow_summary"] = flow_summary
                _merge_unique(cur["inclusions"], inclusions)
                _merge_unique(cur["exclusions"], exclusions)
                _merge_unique(cur["branches"], branches)
                cur["calculation"] = cur["calculation"] or calc_text
                cur["dedup"] = cur["dedup"] or dedup_text
                cur["_score"] += scored[ev.id][0]
            else:
                stages.append(
                    {
                        "stage": stage,
                        "flow_summary": flow_summary,
                        "inclusions": inclusions,
                        "exclusions": exclusions,
                        "branches": branches,
                        "calculation": calc_text,
                        "dedup": dedup_text,
                        "stage_role_label": _STAGE_ROLE_LABELS.get(role, "处理环节"),
                        "_score": scored[ev.id][0],
                    }
                )
                last_title = stage
        if stages:
            flows.append((sum(s["_score"] for s in stages), stages))

    flows.sort(key=lambda x: x[0], reverse=True)
    flows = flows[:2]

    out: List[Dict[str, Any]] = []
    seen = set()
    for f_idx, (_score, stages) in enumerate(flows):
        label = f"加工流程{_FLOW_ORDINALS[f_idx] if f_idx < len(_FLOW_ORDINALS) else f_idx + 1}"
        order = 0
        for st in stages:
            sig = (st["stage"], st["flow_summary"][:60])
            if sig in seen:
                continue
            seen.add(sig)
            order += 1
            item = {k: v for k, v in st.items() if k != "_score"}
            item["flow_label"] = label
            item["order"] = order
            out.append(item)
            if len(out) >= limit:
                return out
    return out


def get_asset_backing_detail(session: Session, asset_code: str) -> Dict[str, Any]:
    """仅返回业务视角的实证支撑摘要（条款原文 + 通俗加工步骤 + 实证数量），绝不暴露代码/表名。"""
    rule = session.query(BusinessRuleAssetModel).filter(BusinessRuleAssetModel.code == asset_code).first()
    if rule is not None:
        clauses = (
            session.query(RegulatoryClauseModel)
            .filter(RegulatoryClauseModel.id.in_(rule.clause_ids_json or []))
            .all()
            if (rule.clause_ids_json or [])
            else []
        )
        revisions = (
            session.query(AssetRevisionModel)
            .filter(AssetRevisionModel.asset_id == rule.id)
            .order_by(AssetRevisionModel.created_at.desc())
            .all()
        )
        return {
            "asset_kind": "RULE",
            "code": rule.code,
            "title": rule.title,
            "reconcile_status": rule.reconcile_status,
            "status": rule.status,
            "logic_backing_count": len(rule.evidence_ids_json or []),
            "processing_steps": summarize_processing_flow(session, list(rule.evidence_ids_json or []), limit=8),
            "clause_references": [
                {"chapter": c.chapter, "clause_title": c.clause_title, "requirement": c.business_requirement}
                for c in clauses
            ],
            "revisions": [
                {"editor": rv.editor, "revision_kind": rv.revision_kind, "reason": rv.reason}
                for rv in revisions
            ],
        }
    cal = session.query(BusinessCaliberAssetModel).filter(BusinessCaliberAssetModel.code == asset_code).first()
    if cal is not None:
        clauses = (
            session.query(RegulatoryClauseModel)
            .filter(RegulatoryClauseModel.id.in_(cal.clause_ids_json or []))
            .all()
            if (cal.clause_ids_json or [])
            else []
        )
        return {
            "asset_kind": "CALIBER",
            "code": cal.code,
            "title": cal.metric_name,
            "reconcile_status": cal.reconcile_status,
            "status": cal.status,
            "logic_backing_count": len(cal.evidence_ids_json or []),
            "processing_steps": summarize_processing_flow(session, list(cal.evidence_ids_json or []), limit=8),
            "clause_references": [
                {"chapter": c.chapter, "clause_title": c.clause_title, "requirement": c.business_requirement}
                for c in clauses
            ],
            "conflict_notes": cal.conflict_notes,
        }
    raise KeyError("Business asset not found")


_HUMAN_REVISION_KINDS = frozenset({"WORDING_EDIT", "CONFIRM", "HUMAN_EDIT", "MANUAL_EDIT"})


def list_asset_revisions(
    session: Session,
    *,
    asset_kind: Optional[str] = None,
    revision_kind: Optional[str] = None,
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    human_only: bool = False,
    limit: int = 120,
) -> Dict[str, Any]:
    """跨业务资产的修订与人工确权动态时间线（严守纯业务去技术化边界）。"""
    cap_rows = session.query(BusinessCapabilityModel).all()
    cap_names = {c.id: c.name for c in cap_rows}
    cap_regimes = _compute_capability_regimes(session, cap_rows)

    rules_by_id = {r.id: r for r in session.query(BusinessRuleAssetModel).all()}
    cals_by_id = {c.id: c for c in session.query(BusinessCaliberAssetModel).all()}
    caps_by_id = {c.id: c for c in cap_rows}

    query = session.query(AssetRevisionModel).order_by(AssetRevisionModel.created_at.desc())
    if asset_kind and asset_kind.strip().upper() not in ("", "ALL"):
        query = query.filter(AssetRevisionModel.asset_kind == asset_kind.strip().upper())

    rows = query.limit(max(limit * 4, 500)).all()
    target_rev_kind = (revision_kind or "").strip().upper()

    summary = {
        "total": 0,
        "wording_edits": 0,
        "confirmations": 0,
        "other_events": 0,
        "assets_touched": 0,
    }
    touched_assets = set()
    items: List[Dict[str, Any]] = []

    for rev in rows:
        meta_code = ""
        meta_title = ""
        meta_cap = ""
        meta_regime = "通用监管"
        meta_sub_domain = ""
        meta_theme = ""
        meta_reconcile = ""
        meta_version = 1
        meta_status = ""

        if rev.asset_kind == "RULE" and rev.asset_id in rules_by_id:
            r = rules_by_id[rev.asset_id]
            meta_code = r.code
            meta_title = r.title
            meta_cap = cap_names.get(r.capability_id, "")
            meta_regime = cap_regimes.get(r.capability_id, "通用监管")
            meta_sub_domain = r.sub_domain or ""
            meta_theme = r.subject_theme or ""
            meta_reconcile = r.reconcile_status
            meta_version = r.current_version
            meta_status = r.status
        elif rev.asset_kind == "CALIBER" and rev.asset_id in cals_by_id:
            c = cals_by_id[rev.asset_id]
            meta_code = c.code
            meta_title = c.metric_name
            meta_cap = cap_names.get(c.capability_id, "")
            meta_regime = cap_regimes.get(c.capability_id, "通用监管")
            meta_sub_domain = c.sub_domain or ""
            meta_theme = c.subject_theme or ""
            meta_reconcile = c.reconcile_status
            meta_version = c.current_version
            meta_status = c.status
        elif rev.asset_id in caps_by_id:
            cap = caps_by_id[rev.asset_id]
            meta_code = cap.code
            meta_title = cap.name
            meta_cap = cap.name
            meta_regime = cap_regimes.get(cap.id, "通用监管")
            meta_version = cap.current_version
            meta_status = cap.status

        if not matches_agency_or_regime(meta_regime, agency=agency, regime=regime):
            continue

        summary["total"] += 1
        touched_assets.add((rev.asset_kind, rev.asset_id))
        if rev.revision_kind in ("WORDING_EDIT", "HUMAN_EDIT", "MANUAL_EDIT"):
            summary["wording_edits"] += 1
        elif rev.revision_kind == "CONFIRM":
            summary["confirmations"] += 1
        else:
            summary["other_events"] += 1

        if human_only and rev.revision_kind not in _HUMAN_REVISION_KINDS:
            continue
        if target_rev_kind and target_rev_kind != "ALL" and rev.revision_kind != target_rev_kind:
            continue

        if len(items) < limit:
            items.append(
                {
                    "id": rev.id,
                    "asset_kind": rev.asset_kind,
                    "asset_id": rev.asset_id,
                    "asset_code": meta_code or rev.asset_id[:8],
                    "asset_title": meta_title or "已归档业务资产",
                    "capability_name": meta_cap,
                    "regime": meta_regime,
                    "agency": agency_for_regime(meta_regime),
                    "sub_domain": meta_sub_domain,
                    "subject_theme": meta_theme,
                    "reconcile_status": meta_reconcile,
                    "current_version": meta_version,
                    "asset_status": meta_status,
                    "revision_kind": rev.revision_kind,
                    "editor": rev.editor,
                    "reason": rev.reason,
                    "old_value": rev.old_value_json,
                    "new_value": rev.new_value_json,
                    "created_at": rev.created_at.isoformat() if rev.created_at else None,
                }
            )

    summary["assets_touched"] = len(touched_assets)
    return {"summary": summary, "revisions": items}


def export_rule_studio_handoff(
    session: Session,
    *,
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    statuses: Tuple[str, ...] = ("UNREALIZED", "DIVERGENT"),
) -> Dict[str, Any]:
    """生成标准化整改条款交接包，将未落地（UNREALIZED）与口径分歧（DIVERGENT）项映射至 data-rule-studio 监管编码。"""
    ledger = list_reconcile_ledger(session, agency=agency, regime=regime)
    allowed = {s.upper() for s in statuses}
    clauses_by_id = {c.id: c for c in session.query(RegulatoryClauseModel).all()}
    rules_by_id = {r.id: r for r in session.query(BusinessRuleAssetModel).all()}
    cals_by_id = {c.id: c for c in session.query(BusinessCaliberAssetModel).all()}

    handoff_items: List[Dict[str, Any]] = []
    for item in ledger["items"]:
        if item["reconcile_status"] not in allowed:
            continue
        clause_ids: List[str] = []
        if item["asset_kind"] == "RULE" and item["id"] in rules_by_id:
            clause_ids = list(rules_by_id[item["id"]].clause_ids_json or [])
        elif item["asset_kind"] == "CALIBER" and item["id"] in cals_by_id:
            clause_ids = list(cals_by_id[item["id"]].clause_ids_json or [])
        clauses = [
            {
                "chapter": clauses_by_id[cid].chapter,
                "clause_title": clauses_by_id[cid].clause_title,
                "requirement": clauses_by_id[cid].business_requirement,
            }
            for cid in clause_ids
            if cid in clauses_by_id
        ]
        handoff_items.append(
            {
                "asset_kind": item["asset_kind"],
                "asset_code": item["code"],
                "title": item["name"],
                "capability_name": item["capability_name"],
                "agency": item["agency"],
                "regime": item["regime"],
                "rule_studio_regulator": rule_studio_regulator_code(item["regime"]),
                "subject_theme": item["subject_theme"],
                "reconcile_status": item["reconcile_status"],
                "business_summary": item["summary"],
                "regulatory_clauses": clauses,
            }
        )

    return {
        "schema": "data-agent.rule-studio-handoff.v1",
        "source_service": "data-biz-copilot",
        "target_service": "data-rule-studio",
        "filter": {"agency": agency or "ALL", "regime": regime or "ALL", "statuses": sorted(allowed)},
        "total_items": len(handoff_items),
        "items": handoff_items,
    }
