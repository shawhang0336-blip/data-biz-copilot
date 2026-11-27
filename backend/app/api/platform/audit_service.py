"""全链路合规审计服务（Layer 6: api/platform）：EventLog SHA-256 验链、双门禁拦截统计与合规证据归档。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.infra.db_tables import (
    AssetRevisionModel,
    BusinessCaliberAssetModel,
    BusinessCapabilityModel,
    BusinessRuleAssetModel,
    CopilotMessageModel,
    CopilotSessionModel,
    EventModel,
    IngestionJobModel,
    RunModel,
    SourceMaterialModel,
)
from app.kernel.event_log import GENESIS_HASH, build_trace, compute_event_hash
from app.policy.dc_plan import fold, get_plan


def _classify_run_kind(run: RunModel, job_by_run: Dict[str, IngestionJobModel]) -> Tuple[str, str]:
    spec_id = (run.spec_id or "").strip().lower()
    if run.id in job_by_run or "batch" in spec_id:
        return "BATCH_INGEST", "千级工程批摄入"
    if "code" in spec_id:
        return "CODE_PARSE", "单脚本逻辑解析"
    if "doc" in spec_id:
        return "DOC_PARSE", "制度文档解析"
    return "ASSET_SYNTHESIS", "双源资产碰撞合成"


def _classify_bus_guard(tool: str, reason: str) -> str:
    low = f"{tool} {reason}".lower()
    if any(k in low for k in ("de_jargon", "技术词", "表名", "sql", "物理", "蛇形", "jargon")):
        return "DE_JARGON"
    if any(k in low for k in ("evidence", "白名单", "实证", "backing", "allowed_")):
        return "EVIDENCE_BACKING"
    return "CONTRACT_GUARD"


def _verify_events_chain(run_id: str, events: List[EventModel]) -> Tuple[bool, Optional[int], List[Dict[str, Any]]]:
    prev_hash = GENESIS_HASH
    chain_valid = True
    broken_seq: Optional[int] = None
    verified: List[Dict[str, Any]] = []
    for ev in events:
        payload = ev.payload_json if isinstance(ev.payload_json, dict) else {}
        prev_ok = ev.prev_hash == prev_hash
        expected_hash = compute_event_hash(run_id, ev.kind, ev.node, payload, prev_hash)
        hash_ok = ev.event_hash == expected_hash
        row_ok = prev_ok and hash_ok
        if not row_ok and broken_seq is None:
            chain_valid = False
            broken_seq = ev.seq
        verified.append(
            {
                "seq": ev.seq,
                "kind": ev.kind,
                "node": ev.node,
                "payload": payload,
                "prev_hash": ev.prev_hash,
                "event_hash": ev.event_hash,
                "chain_ok": row_ok,
                "expected_hash": None if row_ok else expected_hash,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            }
        )
        prev_hash = ev.event_hash if row_ok else expected_hash
    return chain_valid, broken_seq, verified


def _copilot_governance_stats(session: Session) -> Dict[str, int]:
    sessions_count = session.query(CopilotSessionModel).count()
    assistant_msgs = (
        session.query(CopilotMessageModel)
        .filter(CopilotMessageModel.role == "assistant")
        .all()
    )
    disclosed_divergent = 0
    disclosed_unrealized = 0
    for msg in assistant_msgs:
        for disc in msg.disclosures_json or []:
            if not isinstance(disc, dict):
                continue
            dk = str(disc.get("kind") or disc.get("status") or "").upper()
            if "DIVERGENT" in dk:
                disclosed_divergent += 1
            elif "UNREALIZED" in dk:
                disclosed_unrealized += 1
    return {
        "total_sessions": sessions_count,
        "total_answers": len(assistant_msgs),
        "disclosed_divergent": disclosed_divergent,
        "disclosed_unrealized": disclosed_unrealized,
    }


def build_audit_overview(
    session: Session,
    *,
    run_kind: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """全链路 EventLog 合规审计总览：一次性纳管批摄入、资产合成与单源解析账本并实时验链。"""
    runs = (
        session.query(RunModel)
        .order_by(RunModel.created_at.desc())
        .limit(max(limit, 250))
        .all()
    )
    verified_at = datetime.now(timezone.utc).isoformat()
    copilot_stats = _copilot_governance_stats(session)
    manual_confirmations = (
        session.query(AssetRevisionModel)
        .filter(AssetRevisionModel.revision_kind.in_(["CONFIRM", "WORDING_EDIT"]))
        .count()
    )

    if not runs:
        return {
            "summary": {
                "total_runs": 0,
                "batch_runs": 0,
                "synthesis_runs": 0,
                "chain_valid_runs": 0,
                "chain_broken_runs": 0,
                "total_events": 0,
                "total_bus_rejections": 0,
                "de_jargon_rejections": 0,
                "evidence_rejections": 0,
                "total_pauses_and_resumes": 0,
                "total_llm_calls": 0,
                "total_tokens": 0,
                "manual_governance_actions": manual_confirmations,
                "copilot_governance": copilot_stats,
                "verified_at": verified_at,
            },
            "runs": [],
        }

    run_ids = [r.id for r in runs]
    events_by_run: Dict[str, List[EventModel]] = defaultdict(list)
    for ev in (
        session.query(EventModel)
        .filter(EventModel.run_id.in_(run_ids))
        .order_by(EventModel.run_id, EventModel.seq)
        .all()
    ):
        events_by_run[ev.run_id].append(ev)

    job_by_run = {
        j.run_id: j
        for j in session.query(IngestionJobModel).filter(IngestionJobModel.run_id.in_(run_ids)).all()
    }

    target_kind = (run_kind or "ALL").strip().upper()
    totals = {
        "total_runs": 0,
        "batch_runs": 0,
        "synthesis_runs": 0,
        "chain_valid_runs": 0,
        "chain_broken_runs": 0,
        "total_events": 0,
        "total_bus_rejections": 0,
        "de_jargon_rejections": 0,
        "evidence_rejections": 0,
        "total_pauses_and_resumes": 0,
        "total_llm_calls": 0,
        "total_tokens": 0,
        "manual_governance_actions": manual_confirmations,
        "copilot_governance": copilot_stats,
        "verified_at": verified_at,
    }
    rows: List[Dict[str, Any]] = []

    for run in runs:
        kind, kind_label = _classify_run_kind(run, job_by_run)
        if target_kind not in ("", "ALL") and kind != target_kind:
            continue
        evs = events_by_run.get(run.id, [])
        state = fold(evs)
        chain_valid, broken_seq, _ = _verify_events_chain(run.id, evs)
        job = job_by_run.get(run.id)

        bus_rej = 0
        de_jargon_rej = 0
        evidence_rej = 0
        pauses_resumes = 0
        for e in evs:
            p = e.payload_json if isinstance(e.payload_json, dict) else {}
            if e.kind == "BUS_REJECTED":
                bus_rej += 1
                g = _classify_bus_guard(str(p.get("tool") or ""), str(p.get("reason") or ""))
                if g == "DE_JARGON":
                    de_jargon_rej += 1
                elif g == "EVIDENCE_BACKING":
                    evidence_rej += 1
            elif e.kind in ("BATCH_PAUSED_BUDGET", "RUN_RESUMED", "TIER_REVIEW_REQUIRED", "BATCH_CANCELLED"):
                pauses_resumes += 1

        res = state.result_json if isinstance(state.result_json, dict) else {}
        llm_calls = int(job.llm_calls_used if job else (res.get("llm_calls") or 0))
        tokens_used = int(job.tokens_used if job else (res.get("tokens_used") or 0))
        elapsed_seconds = int(job.elapsed_seconds if job else (res.get("elapsed_seconds") or 0))

        title = (
            (job.name or f"批量工程接入 ({job.total_files} 份)")
            if job
            else f"{kind_label} #{run.id[:8]}"
        )

        totals["total_runs"] += 1
        if kind == "BATCH_INGEST":
            totals["batch_runs"] += 1
        elif kind == "ASSET_SYNTHESIS":
            totals["synthesis_runs"] += 1
        if chain_valid:
            totals["chain_valid_runs"] += 1
        else:
            totals["chain_broken_runs"] += 1
        totals["total_events"] += len(evs)
        totals["total_bus_rejections"] += bus_rej
        totals["de_jargon_rejections"] += de_jargon_rej
        totals["evidence_rejections"] += evidence_rej
        totals["total_pauses_and_resumes"] += pauses_resumes
        totals["total_llm_calls"] += llm_calls
        totals["total_tokens"] += tokens_used

        if len(rows) < limit:
            rows.append(
                {
                    "run_id": run.id,
                    "spec_id": run.spec_id,
                    "run_kind": kind,
                    "kind_label": kind_label,
                    "title": title,
                    "status": state.status,
                    "current_node": state.current_node,
                    "event_count": len(evs),
                    "chain_valid": chain_valid,
                    "broken_seq": broken_seq,
                    "tail_hash": evs[-1].event_hash if evs else GENESIS_HASH,
                    "bus_rejected_count": bus_rej,
                    "de_jargon_rejected_count": de_jargon_rej,
                    "evidence_rejected_count": evidence_rej,
                    "pause_resume_count": pauses_resumes,
                    "llm_calls": llm_calls,
                    "tokens_used": tokens_used,
                    "elapsed_seconds": elapsed_seconds,
                    "job_summary": (
                        {
                            "job_id": job.id,
                            "source_mode": job.source_mode,
                            "total_files": job.total_files,
                            "tier1_count": job.tier1_count,
                            "tier2_count": job.tier2_count,
                            "tier3_count": job.tier3_count,
                            "template_clone_count": job.template_clone_count,
                            "completed_count": job.completed_count,
                            "failed_count": job.failed_count,
                        }
                        if job
                        else None
                    ),
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                    "updated_at": run.updated_at.isoformat() if run.updated_at else None,
                }
            )

    return {"summary": totals, "runs": rows}


def build_audit_run_detail(session: Session, run: RunModel) -> Dict[str, Any]:
    """单次 Run 全链路合规审计档案（逐条验链 + 节点耗时瀑布 + 去技术化/实证双门禁拦截 + 端到端指纹）。"""
    evs = (
        session.query(EventModel)
        .filter(EventModel.run_id == run.id)
        .order_by(EventModel.seq.asc())
        .all()
    )
    job = session.query(IngestionJobModel).filter(IngestionJobModel.run_id == run.id).first()
    kind, kind_label = _classify_run_kind(run, {run.id: job} if job else {})
    state = fold(evs)
    chain_valid, broken_seq, verified_events = _verify_events_chain(run.id, evs)
    trace = build_trace(evs, run_id=run.id, plan=get_plan(run.spec_id)).as_dict()

    bus_rejections: List[Dict[str, Any]] = []
    governance_actions: List[Dict[str, Any]] = []
    for ev in verified_events:
        ek = ev["kind"]
        p = ev["payload"]
        if ek == "BUS_REJECTED":
            tool = str(p.get("tool") or "")
            reason = str(p.get("reason") or "")
            bus_rejections.append(
                {
                    "seq": ev["seq"],
                    "node": ev["node"],
                    "tool": tool,
                    "guard_type": _classify_bus_guard(tool, reason),
                    "reason": reason,
                    "created_at": ev["created_at"],
                    "event_hash": ev["event_hash"],
                }
            )
        elif ek in (
            "TIER_REVIEW_REQUIRED",
            "BATCH_STARTED",
            "BATCH_PAUSED_BUDGET",
            "BATCH_CANCELLED",
            "RUN_RESUMED",
            "NODE_RETRY",
            "GO_REDIRECT",
        ):
            governance_actions.append(
                {
                    "seq": ev["seq"],
                    "kind": ek,
                    "node": ev["node"],
                    "summary": str(p.get("reason") or p.get("message") or ek),
                    "payload": p,
                    "created_at": ev["created_at"],
                    "event_hash": ev["event_hash"],
                }
            )

    materials_q = (
        session.query(SourceMaterialModel)
        .filter(SourceMaterialModel.batch_job_id == job.id)
        .order_by(SourceMaterialModel.created_at.desc())
        .limit(40)
        .all()
        if job
        else session.query(SourceMaterialModel).order_by(SourceMaterialModel.created_at.desc()).limit(20).all()
    )
    source_fingerprints = [
        {
            "title": m.title,
            "material_kind": m.material_kind,
            "file_type": m.file_type,
            "tier": m.tier,
            "status": m.status,
            "sha256": m.sha256,
        }
        for m in materials_q
    ]

    cap_cnt = session.query(BusinessCapabilityModel).count()
    rule_cnt = session.query(BusinessRuleAssetModel).count()
    cal_cnt = session.query(BusinessCaliberAssetModel).count()
    div_cnt = (
        session.query(BusinessRuleAssetModel).filter(BusinessRuleAssetModel.reconcile_status == "DIVERGENT").count()
        + session.query(BusinessCaliberAssetModel).filter(BusinessCaliberAssetModel.reconcile_status == "DIVERGENT").count()
    )
    unr_cnt = (
        session.query(BusinessRuleAssetModel).filter(BusinessRuleAssetModel.reconcile_status == "UNREALIZED").count()
        + session.query(BusinessCaliberAssetModel).filter(BusinessCaliberAssetModel.reconcile_status == "UNREALIZED").count()
    )
    conf_cnt = (
        session.query(BusinessRuleAssetModel).filter(BusinessRuleAssetModel.status == "CONFIRMED").count()
        + session.query(BusinessCaliberAssetModel).filter(BusinessCaliberAssetModel.status == "CONFIRMED").count()
    )

    res = state.result_json if isinstance(state.result_json, dict) else {}
    return {
        "run_id": run.id,
        "spec_id": run.spec_id,
        "run_kind": kind,
        "kind_label": kind_label,
        "title": (job.name or f"批量工程接入 ({job.total_files} 份)") if job else f"{kind_label} #{run.id[:8]}",
        "status": state.status,
        "current_node": state.current_node,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "chain_valid": chain_valid,
        "broken_seq": broken_seq,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "budget": {
            "llm_calls": int(job.llm_calls_used if job else (res.get("llm_calls") or 0)),
            "tokens_used": int(job.tokens_used if job else (res.get("tokens_used") or 0)),
            "elapsed_seconds": int(job.elapsed_seconds if job else (res.get("elapsed_seconds") or 0)),
        },
        "events": verified_events,
        "trace": trace,
        "bus_rejections": bus_rejections,
        "governance_actions": governance_actions,
        "fingerprints": {
            "source_materials": source_fingerprints,
            "event_chain": {
                "genesis_hash": GENESIS_HASH,
                "first_seq": verified_events[0]["seq"] if verified_events else None,
                "last_seq": verified_events[-1]["seq"] if verified_events else None,
                "tail_hash": verified_events[-1]["event_hash"] if verified_events else GENESIS_HASH,
                "event_count": len(verified_events),
                "chain_valid": chain_valid,
                "broken_seq": broken_seq,
            },
            "job_summary": (
                {
                    "job_id": job.id,
                    "name": job.name,
                    "source_mode": job.source_mode,
                    "status": job.status,
                    "total_files": job.total_files,
                    "tier1_count": job.tier1_count,
                    "tier2_count": job.tier2_count,
                    "tier3_count": job.tier3_count,
                    "template_clone_count": job.template_clone_count,
                    "completed_count": job.completed_count,
                    "failed_count": job.failed_count,
                }
                if job
                else None
            ),
            "asset_governance": {
                "capabilities": cap_cnt,
                "rules": rule_cnt,
                "calibers": cal_cnt,
                "confirmed": conf_cnt,
                "divergent": div_cnt,
                "unrealized": unr_cnt,
            },
        },
    }
