"""纯业务资产层只读视图查询（资产层 Layer 4）。

严守隐私与去技术化边界：任何返回前端或问答层的视图对象均不输出 `LogicEvidenceModel.internal_locator_json`
中的物理表名、字段名或 SQL 语句，只呈现业务表述与实证支撑状态。
"""

from typing import Any, Dict, List, Optional

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
from app.infra.retrieval import infer_regulatory_regime


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
    return {
        "id": row.id,
        "capability_id": row.capability_id,
        "capability_name": capability_name,
        "regime": regime,
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
    return {
        "id": row.id,
        "capability_id": row.capability_id,
        "capability_name": capability_name,
        "regime": regime,
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


def list_source_materials(session: Session) -> List[Dict[str, Any]]:
    rows = session.query(SourceMaterialModel).order_by(SourceMaterialModel.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "material_kind": r.material_kind,
            "file_type": r.file_type,
            "status": r.status,
            "summary": r.summary,
            "item_count": r.item_count,
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


def list_capabilities(session: Session) -> List[Dict[str, Any]]:
    rows = session.query(BusinessCapabilityModel).order_by(BusinessCapabilityModel.name.asc()).all()
    lineage = _compute_business_lineage(session)
    regimes = _compute_capability_regimes(session, rows)
    return [
        {
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "regime": regimes.get(r.id, "通用监管"),
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
        }
        for r in rows
    ]


def list_business_rules(
    session: Session,
    *,
    capability_id: Optional[str] = None,
    rule_category: Optional[str] = None,
    reconcile_status: Optional[str] = None,
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
    return [_rule_to_dict(r, caps.get(r.capability_id, ""), regimes.get(r.capability_id, "通用监管")) for r in rows]


def list_business_calibers(
    session: Session,
    *,
    capability_id: Optional[str] = None,
    reconcile_status: Optional[str] = None,
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
    return [_caliber_to_dict(r, caps.get(r.capability_id, ""), regimes.get(r.capability_id, "通用监管")) for r in rows]


def list_reconcile_ledger(session: Session) -> Dict[str, Any]:
    rules = list_business_rules(session)
    calibers = list_business_calibers(session)
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
                "reconcile_status": c["reconcile_status"],
                "status": c["status"],
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
                "reconcile_status": r["reconcile_status"],
                "status": r["status"],
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
    return {
        "materials": materials,
        "capabilities": capabilities,
        "rules": rules,
        "calibers": calibers,
        "reconcile": ledger,
        "stats": {
            "capability_count": len(capabilities),
            "covered_capabilities": sum(1 for c in capabilities if c["coverage_status"] == "COVERED"),
            "unrealized_capabilities": sum(1 for c in capabilities if c["coverage_status"] == "UNREALIZED"),
            "rule_count": len(rules),
            "caliber_count": len(calibers),
            "confirmed_count": sum(1 for x in rules + calibers if x["status"] == "CONFIRMED"),
        },
    }


def get_asset_backing_detail(session: Session, asset_code: str) -> Dict[str, Any]:
    """仅返回业务视角的实证支撑摘要（条款原文 + 已验证逻辑环节数量），绝不暴露代码/表名。"""
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
            "clause_references": [
                {"chapter": c.chapter, "clause_title": c.clause_title, "requirement": c.business_requirement}
                for c in clauses
            ],
            "conflict_notes": cal.conflict_notes,
        }
    raise KeyError("Business asset not found")
