"""双源接入落库与「代码 × 监管材料」纯业务资产融合合成引擎（资产层 Layer 4）。"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infra.blob_store import LocalBlobStore
from app.infra.config import Settings, get_budget, get_effective_settings, get_settings
from app.infra.db_tables import (
    BusinessCaliberAssetModel,
    BusinessCapabilityModel,
    BusinessRuleAssetModel,
    LogicEvidenceModel,
    RegulatoryClauseModel,
    RunModel,
    SourceMaterialModel,
)
from app.infra.errors import WorkflowError
from app.infra.retrieval import infer_regulatory_regime, tokenize_for_search
from app.infra.llm import (
    RealDesignLlm,
    call_llm_validated,
    configure_llm_concurrency,
    llm_execution_scope,
    submit_with_context,
)
from app.kernel.event_log import append_event
from app.kernel.run import create_run as kernel_create_run
from app.ingestion.builtin_pack import (
    BUILTIN_CODE_SOURCES,
    BUILTIN_REGULATORY_DOCS,
    collect_real_sac_materials,
)
from app.ingestion.code_parser import parse_code_logic
from app.ingestion.doc_parser import parse_regulatory_document
from app.policy.dc_bus import build_run_bus, default_bus, detect_technical_jargon, sanitize_business_text


def _ensure_business_clean(bus, tool_name: str, payload: Dict[str, Any], allowed_evidence_ids: List[str]) -> Dict[str, Any]:
    """确保每张合成资产卡经过 Bus 去技术化门禁与实证白名单门禁校验。"""
    reason = bus.adjudicate(tool_name, payload, {"allowed_evidence_ids": allowed_evidence_ids})
    if reason:
        cleaned = {}
        for k, v in payload.items():
            cleaned[k] = sanitize_business_text(v) if isinstance(v, str) else v
        retry_reason = bus.adjudicate(tool_name, cleaned, {"allowed_evidence_ids": allowed_evidence_ids})
        if retry_reason:
            raise ValueError(retry_reason)
        return cleaned
    return payload


def _resolve_optional_llm(session: Session, settings: Settings, explicit_llm: Any = None) -> Any:
    if explicit_llm is not None:
        return explicit_llm
    eff = get_effective_settings(session, settings)
    if eff.llm_provider != "scripted" and eff.llm_api_key:
        return RealDesignLlm(eff)
    return None


def _match_relevant_clauses(
    rule_text: str,
    clauses: List[RegulatoryClauseModel],
    allowed_kinds: set,
) -> List[str]:
    """基于关键词重合度将每条规则精确关联到对应制度条款，而非把全领域条款全部挂载。"""
    candidates = [c for c in clauses if c.clause_kind in allowed_kinds] or list(clauses)
    if not candidates:
        return []
    tokens = [w for w in re.findall(r"[\u4e00-\u9fff]{2,}", rule_text) if len(w) >= 2]
    scored = []
    for c in candidates:
        hay = f"{c.clause_title} {c.business_requirement}"
        hits = sum(1 for t in tokens if t in hay)
        if hits > 0:
            scored.append((c.id, hits))
    if scored:
        scored.sort(key=lambda x: x[1], reverse=True)
        return [cid for cid, _ in scored[:3]]
    return [c.id for c in candidates[:2]]


def _resolve_llm_model_name(active_llm: Any, settings: Settings) -> str:
    if active_llm is None:
        return "scripted"
    nested = getattr(active_llm, "settings", None)
    return str(
        getattr(active_llm, "model", None)
        or getattr(nested, "llm_model", None)
        or settings.llm_model
        or "configured"
    )


def _parse_material_payload(
    title: str,
    material_kind: str,
    content: bytes,
    *,
    active_llm: Any,
    bus: Any,
) -> List[Any]:
    if material_kind == "REGULATORY_DOC":
        return parse_regulatory_document(
            title,
            content,
            llm=active_llm,
            bus=bus,
        )
    return parse_code_logic(
        title,
        content,
        llm=active_llm,
        bus=bus,
    )


def _persist_material_items(
    session: Session,
    material: SourceMaterialModel,
    material_kind: str,
    parsed_items: List[Any],
) -> None:
    if material_kind == "REGULATORY_DOC":
        for c in parsed_items:
            session.add(
                RegulatoryClauseModel(
                    id=str(uuid.uuid4()),
                    material_id=material.id,
                    chapter=c.chapter,
                    business_domain=c.business_domain,
                    clause_title=sanitize_business_text(c.clause_title),
                    clause_kind=c.clause_kind,
                    business_requirement=sanitize_business_text(c.business_requirement),
                    audience=c.audience,
                    metrics_mentioned_json=c.metrics_mentioned,
                    source_locator_json=c.source_locator,
                )
            )
        material.item_count = len(parsed_items)
        if any((getattr(c, "source_locator", None) or {}).get("plan_applied") for c in parsed_items):
            material.summary = f"已完成 AI 结构规划与分块精炼，提取 {len(parsed_items)} 条纯业务规范与口径条款。"
        else:
            material.summary = f"已从制度文档提取 {len(parsed_items)} 条业务规范与口径条款。"
    else:
        stored = 0
        for ev in parsed_items:
            if (
                session.query(LogicEvidenceModel)
                .filter(LogicEvidenceModel.logic_fingerprint == ev.logic_fingerprint)
                .first()
            ):
                continue
            row = LogicEvidenceModel(
                id=str(uuid.uuid4()),
                material_id=material.id,
                logic_fingerprint=ev.logic_fingerprint,
                business_domain_hint=ev.business_domain_hint,
                target_concept_hint=ev.target_concept_hint,
                source_summary=ev.source_summary,
                inclusion_predicates_json=ev.inclusion_predicates,
                exclusion_predicates_json=ev.exclusion_predicates,
                branch_cases_json=ev.branch_cases,
                calculation_kind=ev.calculation_kind,
                calculation_summary=ev.calculation_summary,
                dedup_grain_hint=ev.dedup_grain_hint,
                internal_locator_json=ev.internal_locator,
            )
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
                stored += 1
            except IntegrityError:
                pass
        material.item_count = stored
        if any((getattr(ev, "internal_locator", None) or {}).get("plan_applied") for ev in parsed_items):
            material.summary = (
                f"已完成 AI 逻辑骨架规划与分阶段提取，静默沉淀 {stored} 组底层逻辑实证（物理对象已隔离封存，不对外展示）。"
            )
        else:
            material.summary = f"已静默提取 {stored} 组底层逻辑实证（物理对象已隔离封存，不对外展示）。"


def _ingest_materials_batch(
    session: Session,
    items: List[Tuple[str, str, str, bytes]],
    *,
    settings: Settings,
    llm: Any = None,
) -> List[SourceMaterialModel]:
    """并发解析一批双源材料，并在主线程中顺序落库（兼顾多核 AI 提速与 SQLite 线程安全）。"""
    active_llm = _resolve_optional_llm(session, settings, llm)
    blob_store = LocalBlobStore(settings.blob_dir)
    seen_digests = set()
    pending: List[Tuple[str, str, str, bytes, str, str]] = []
    materials: List[SourceMaterialModel] = []

    for title, material_kind, file_type, raw in items:
        raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        digest = hashlib.sha256(raw_bytes).hexdigest()
        if digest in seen_digests:
            continue
        seen_digests.add(digest)
        existing = session.query(SourceMaterialModel).filter(SourceMaterialModel.sha256 == digest).first()
        if existing is not None:
            materials.append(existing)
            continue
        blob_uri = blob_store.put(raw_bytes)
        pending.append((title, material_kind, file_type, raw_bytes, digest, blob_uri))

    if not pending:
        return materials

    parsed_by_idx: List[List[Any]] = [[] for _ in pending]
    if active_llm is not None:
        budget = get_budget(session)
        workers = max(1, min(6, int(budget.get("max_llm_concurrency", 4))))
        configure_llm_concurrency(int(budget.get("max_llm_concurrency", 4)))
        bus = build_run_bus(budget)
        with llm_execution_scope(bus=bus):
            if len(pending) == 1:
                title, kind, _, raw_bytes, _, _ = pending[0]
                parsed_by_idx[0] = _parse_material_payload(title, kind, raw_bytes, active_llm=active_llm, bus=bus)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [
                        (
                            idx,
                            submit_with_context(
                                pool,
                                _parse_material_payload,
                                title,
                                kind,
                                raw_bytes,
                                active_llm=active_llm,
                                bus=bus,
                            ),
                        )
                        for idx, (title, kind, _, raw_bytes, _, _) in enumerate(pending)
                    ]
                    for idx, fut in futures:
                        parsed_by_idx[idx] = fut.result()
    else:
        for idx, (title, kind, _, raw_bytes, _, _) in enumerate(pending):
            parsed_by_idx[idx] = _parse_material_payload(title, kind, raw_bytes, active_llm=None, bus=None)

    for (title, kind, file_type, _, digest, blob_uri), parsed_items in zip(pending, parsed_by_idx):
        candidate = SourceMaterialModel(
            id=str(uuid.uuid4()),
            title=title,
            material_kind=kind,
            file_type=file_type,
            sha256=digest,
            blob_uri=blob_uri,
            status="PARSED",
            summary="",
            item_count=0,
        )
        try:
            with session.begin_nested():
                session.add(candidate)
                session.flush()
            material = candidate
        except IntegrityError:
            existing = session.query(SourceMaterialModel).filter(SourceMaterialModel.sha256 == digest).first()
            if existing is None:
                raise
            materials.append(existing)
            continue
        _persist_material_items(session, material, kind, parsed_items)
        session.flush()
        materials.append(material)

    return materials


def ingest_material(
    session: Session,
    *,
    title: str,
    material_kind: str,
    file_type: str,
    content: bytes,
    settings: Optional[Settings] = None,
    llm: Any = None,
    bus: Any = None,
) -> SourceMaterialModel:
    """并发安全地摄入监管制度文档或代码逻辑源，并提取条款或隐身底层实证。"""
    if material_kind not in {"REGULATORY_DOC", "CODE_LOGIC"}:
        raise ValueError(f"Unsupported material_kind: {material_kind}")
    active_settings = settings or get_settings()
    raw_bytes = content if isinstance(content, bytes) else str(content).encode("utf-8")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    blob_uri = LocalBlobStore(active_settings.blob_dir).put(raw_bytes)

    existing = session.query(SourceMaterialModel).filter(SourceMaterialModel.sha256 == digest).first()
    if existing is None:
        candidate = SourceMaterialModel(
            id=str(uuid.uuid4()),
            title=title,
            material_kind=material_kind,
            file_type=file_type,
            sha256=digest,
            blob_uri=blob_uri,
            status="PARSED",
            summary="",
            item_count=0,
        )
        try:
            with session.begin_nested():
                session.add(candidate)
                session.flush()
            material = candidate
        except IntegrityError:
            material = session.query(SourceMaterialModel).filter(SourceMaterialModel.sha256 == digest).first()
            if material is None:
                raise
            return material
    else:
        return existing

    active_llm = _resolve_optional_llm(session, active_settings, llm)
    if active_llm is not None:
        budget = get_budget(session)
        configure_llm_concurrency(int(budget.get("max_llm_concurrency", 4)))
        active_bus = bus or build_run_bus(budget)
        if bus is None:
            with llm_execution_scope(bus=active_bus):
                parsed_items = _parse_material_payload(
                    title, material_kind, raw_bytes, active_llm=active_llm, bus=active_bus
                )
        else:
            parsed_items = _parse_material_payload(
                title, material_kind, raw_bytes, active_llm=active_llm, bus=active_bus
            )
    else:
        parsed_items = _parse_material_payload(title, material_kind, raw_bytes, active_llm=None, bus=None)

    _persist_material_items(session, material, material_kind, parsed_items)
    session.flush()
    return material


def _upsert_capability(
    session: Session,
    *,
    code: str,
    name: str,
    description: str,
    scope_summary: str,
    audience: str,
    coverage_status: str,
    evidence_ids: List[str],
    clause_ids: List[str],
    rule_count: int,
    caliber_count: int,
    confirmed_count: int,
    bus,
) -> BusinessCapabilityModel:
    cleaned = _ensure_business_clean(
        bus,
        "synthesize_business_capability",
        {
            "code": code,
            "name": name,
            "description": description,
            "scope_summary": scope_summary,
            "audience": audience,
        },
        evidence_ids,
    )
    row = session.query(BusinessCapabilityModel).filter(BusinessCapabilityModel.code == code).first()
    if row is None:
        row = BusinessCapabilityModel(
            id=str(uuid.uuid4()),
            code=code,
            name=cleaned["name"],
            description=cleaned["description"],
            scope_summary=cleaned["scope_summary"],
            audience=cleaned["audience"],
            coverage_status=coverage_status,
            rule_count=rule_count,
            caliber_count=caliber_count,
            confirmed_count=confirmed_count,
            evidence_ids_json=evidence_ids,
            clause_ids_json=clause_ids,
            status="DRAFT",
            current_version=1,
        )
        session.add(row)
    else:
        if row.status != "CONFIRMED":
            row.name = cleaned["name"]
            row.description = cleaned["description"]
            row.scope_summary = cleaned["scope_summary"]
            row.audience = cleaned["audience"]
        row.coverage_status = coverage_status
        row.rule_count = rule_count
        row.caliber_count = caliber_count
        row.confirmed_count = confirmed_count
        row.evidence_ids_json = evidence_ids
        row.clause_ids_json = clause_ids
    session.flush()
    return row


def _upsert_rule(
    session: Session,
    *,
    capability_id: str,
    code: str,
    title: str,
    rule_category: str,
    business_condition: str,
    business_action: str,
    business_rationale: str,
    reconcile_status: str,
    evidence_ids: List[str],
    clause_ids: List[str],
    audience: str,
    bus,
) -> BusinessRuleAssetModel:
    cleaned = _ensure_business_clean(
        bus,
        "synthesize_business_rule",
        {
            "code": code,
            "title": title,
            "rule_category": rule_category,
            "business_condition": business_condition,
            "business_action": business_action,
            "business_rationale": business_rationale,
            "evidence_ids": evidence_ids,
        },
        evidence_ids,
    )
    row = session.query(BusinessRuleAssetModel).filter(BusinessRuleAssetModel.code == code).first()
    if row is None:
        row = BusinessRuleAssetModel(
            id=str(uuid.uuid4()),
            capability_id=capability_id,
            code=code,
            title=cleaned["title"],
            rule_category=rule_category,
            business_condition=cleaned["business_condition"],
            business_action=cleaned["business_action"],
            business_rationale=cleaned["business_rationale"],
            reconcile_status=reconcile_status,
            evidence_ids_json=evidence_ids,
            clause_ids_json=clause_ids,
            audience=audience,
            status="DRAFT",
            current_version=1,
        )
        session.add(row)
    else:
        row.capability_id = capability_id
        if row.status != "CONFIRMED":
            row.title = cleaned["title"]
            row.business_condition = cleaned["business_condition"]
            row.business_action = cleaned["business_action"]
            row.business_rationale = cleaned["business_rationale"]
        row.reconcile_status = reconcile_status
        row.evidence_ids_json = evidence_ids
        row.clause_ids_json = clause_ids
        row.audience = audience
    session.flush()
    return row


def _upsert_caliber(
    session: Session,
    *,
    capability_id: str,
    code: str,
    metric_name: str,
    business_definition: str,
    formula_summary: str,
    inclusion_scope: str,
    exclusion_notes: str,
    distinction_notes: str,
    triggers: List[str],
    reconcile_status: str,
    conflict_notes: str,
    evidence_ids: List[str],
    clause_ids: List[str],
    audience: str,
    bus,
) -> BusinessCaliberAssetModel:
    cleaned = _ensure_business_clean(
        bus,
        "synthesize_business_caliber",
        {
            "code": code,
            "metric_name": metric_name,
            "business_definition": business_definition,
            "formula_summary": formula_summary,
            "inclusion_scope": inclusion_scope,
            "exclusion_notes": exclusion_notes,
            "distinction_notes": distinction_notes,
            "conflict_notes": conflict_notes,
            "evidence_ids": evidence_ids,
        },
        evidence_ids,
    )
    row = session.query(BusinessCaliberAssetModel).filter(BusinessCaliberAssetModel.code == code).first()
    if row is None:
        row = BusinessCaliberAssetModel(
            id=str(uuid.uuid4()),
            capability_id=capability_id,
            code=code,
            metric_name=cleaned["metric_name"],
            business_definition=cleaned["business_definition"],
            formula_summary=cleaned["formula_summary"],
            inclusion_scope=cleaned["inclusion_scope"],
            exclusion_notes=cleaned["exclusion_notes"],
            distinction_notes=cleaned["distinction_notes"],
            triggers_json=triggers,
            reconcile_status=reconcile_status,
            conflict_notes=cleaned["conflict_notes"],
            evidence_ids_json=evidence_ids,
            clause_ids_json=clause_ids,
            audience=audience,
            status="DRAFT",
            current_version=1,
        )
        session.add(row)
    else:
        row.capability_id = capability_id
        if row.status != "CONFIRMED":
            row.metric_name = cleaned["metric_name"]
            row.business_definition = cleaned["business_definition"]
            row.formula_summary = cleaned["formula_summary"]
            row.inclusion_scope = cleaned["inclusion_scope"]
            row.exclusion_notes = cleaned["exclusion_notes"]
            row.distinction_notes = cleaned["distinction_notes"]
            row.conflict_notes = cleaned["conflict_notes"]
        row.triggers_json = triggers
        row.reconcile_status = reconcile_status
        row.evidence_ids_json = evidence_ids
        row.clause_ids_json = clause_ids
        row.audience = audience
    session.flush()
    return row


def _summarize_evidence_rules(ev: LogicEvidenceModel, limit: int = 6) -> List[str]:
    items: List[str] = []
    for label, col in (
        ("纳入", ev.inclusion_predicates_json),
        ("排除", ev.exclusion_predicates_json),
        ("分支", ev.branch_cases_json),
    ):
        for rule in col or []:
            if not isinstance(rule, dict):
                continue
            title = sanitize_business_text(str(rule.get("title") or "")).strip()
            cond = sanitize_business_text(str(rule.get("condition") or "")).strip()[:120]
            if title or cond:
                items.append(f"[{label}] {title}: {cond}".strip(": "))
            if len(items) >= limit:
                return items
    return items


def _safe_rule_slug(raw_code: str, prefix: str) -> str:
    """将规则编号归一化为短标识；若超长或命中技术黑话词（如 exists/number），改写为安全哈希编号。"""
    slug = re.sub(r"[^a-z0-9-]+", "-", str(raw_code or "").lower().replace("_", "-")).strip("-")
    if not slug or len(slug) > 32 or detect_technical_jargon(slug):
        digest_src = str(raw_code or prefix).encode("utf-8")
        return f"{prefix}-{hashlib.md5(digest_src).hexdigest()[:8]}"
    return slug


def _build_domain_draft(
    domain_name: str,
    d_clauses: List[RegulatoryClauseModel],
    d_evs: List[LogicEvidenceModel],
) -> Dict[str, Any]:
    """根据单一业务域的监管条款与底层代码实证，构建纯业务确定性资产草案。"""
    ev_ids = [e.id for e in d_evs]
    cl_ids = [c.id for c in d_clauses]
    has_conflict = any(e.calculation_kind == "CONFLICT" for e in d_evs)

    if not d_evs:
        coverage_status = "UNREALIZED"
    elif has_conflict:
        coverage_status = "PARTIAL"
    else:
        coverage_status = "COVERED"

    cap_code = f"cap-{hashlib.md5(domain_name.encode('utf-8')).hexdigest()[:8]}"
    scope_parts = [sanitize_business_text(e.source_summary) for e in d_evs if e.source_summary]
    if not scope_parts:
        scope_parts = [sanitize_business_text(c.business_requirement) for c in d_clauses[:2]]
    scope_summary = "；".join(dict.fromkeys(p for p in scope_parts if p))[:600]
    if d_clauses:
        joined_req = " ".join(line.strip() for line in d_clauses[0].business_requirement.splitlines() if line.strip())
        first_sentence = re.split(r"(?<=[。！？])", joined_req, maxsplit=1)[0].strip()
        description = (first_sentence or joined_req)[:200]
    else:
        description = f"系统当前已落地支撑「{domain_name}」相关业务处理逻辑。"
    audience = d_clauses[0].audience if d_clauses else "业务 / 监管报送"

    rule_drafts: List[Dict[str, Any]] = []
    seen_rule_codes = set()
    dedup_hints: List[str] = []
    dedup_ev_ids: List[str] = []
    # 1) 从底层实证提炼已实现/冲突规则
    for ev in d_evs:
        for item in ev.inclusion_predicates_json or []:
            rc_slug = _safe_rule_slug(item.get("rule_code", ""), "inc")
            rc = f"rule-{cap_code[4:]}-{rc_slug}"
            if rc in seen_rule_codes:
                continue
            seen_rule_codes.add(rc)
            matched_clauses = _match_relevant_clauses(
                f"{item['title']} {item['condition']}", d_clauses, {"INCLUSION", "CAPABILITY"}
            )
            rule_drafts.append(
                {
                    "code": rc,
                    "title": item["title"],
                    "rule_category": "INCLUSION",
                    "business_condition": item["condition"],
                    "business_action": "满足该条件则纳入当期正式业务处理与报送范围",
                    "business_rationale": item["rationale"],
                    "reconcile_status": "VERIFIED" if matched_clauses else "CONSISTENT",
                    "evidence_ids": [ev.id],
                    "clause_ids": matched_clauses,
                    "audience": audience,
                }
            )

        for item in ev.exclusion_predicates_json or []:
            rc_slug = _safe_rule_slug(item.get("rule_code", ""), "exc")
            rc = f"rule-{cap_code[4:]}-{rc_slug}"
            if rc in seen_rule_codes:
                continue
            seen_rule_codes.add(rc)
            matched_clauses = _match_relevant_clauses(
                f"{item['title']} {item['condition']}", d_clauses, {"EXCLUSION"}
            )
            rule_drafts.append(
                {
                    "code": rc,
                    "title": item["title"],
                    "rule_category": "EXCLUSION",
                    "business_condition": item["condition"],
                    "business_action": "自动剔除出当期常规报送结果（必要时单列进入风险待办清单）",
                    "business_rationale": item["rationale"],
                    "reconcile_status": "VERIFIED" if matched_clauses else "CONSISTENT",
                    "evidence_ids": [ev.id],
                    "clause_ids": matched_clauses,
                    "audience": "业务 / 监管报送 / 合规",
                }
            )

        for item in ev.branch_cases_json or []:
            rc = f"rule-{cap_code[4:]}-branch-{hashlib.md5(item['title'].encode('utf-8')).hexdigest()[:6]}"
            if rc in seen_rule_codes:
                continue
            seen_rule_codes.add(rc)
            is_div = "分歧" in item["title"] or ev.calculation_kind == "CONFLICT"
            matched_clauses = _match_relevant_clauses(
                f"{item['title']} {item['condition']}", d_clauses, {"BRANCH", "CALIBER"}
            )
            rule_drafts.append(
                {
                    "code": rc,
                    "title": item["title"],
                    "rule_category": "BRANCH",
                    "business_condition": item["condition"],
                    "business_action": item["action"],
                    "business_rationale": "按业务主体类别或账户属性自动进入差异化处理分支。",
                    "reconcile_status": "DIVERGENT" if is_div else ("VERIFIED" if matched_clauses else "CONSISTENT"),
                    "evidence_ids": [ev.id],
                    "clause_ids": matched_clauses,
                    "audience": audience,
                }
            )

        if ev.dedup_grain_hint:
            clean_hint = sanitize_business_text(ev.dedup_grain_hint).strip()
            if clean_hint and clean_hint not in dedup_hints:
                dedup_hints.append(clean_hint)
            if ev.id not in dedup_ev_ids:
                dedup_ev_ids.append(ev.id)

    if dedup_hints:
        combined_dedup = "；".join(dedup_hints)
        rc = f"rule-{cap_code[4:]}-dedup-{hashlib.md5(combined_dedup.encode('utf-8')).hexdigest()[:6]}"
        if rc not in seen_rule_codes:
            seen_rule_codes.add(rc)
            matched_clauses = _match_relevant_clauses(
                combined_dedup, d_clauses, {"INCLUSION", "CAPABILITY"}
            )
            rule_drafts.append(
                {
                    "code": rc,
                    "title": "唯一记录防重保留规则",
                    "rule_category": "DEDUP",
                    "business_condition": "同一客户、账户或交易在系统加工过程中出现多条状态记录或重复执行时",
                    "business_action": combined_dedup,
                    "business_rationale": "保障报送金额与交易笔数准确唯一，防止同一业务被重复计数。",
                    "reconcile_status": "VERIFIED" if matched_clauses else "CONSISTENT",
                    "evidence_ids": dedup_ev_ids,
                    "clause_ids": matched_clauses,
                    "audience": audience,
                }
            )

    # 2) 制度里有要求但当前代码尚未包含的领域：落 UNREALIZED 规则提示差距
    if not d_evs:
        for cl in d_clauses:
            rc = f"rule-{cap_code[4:]}-unreal-{hashlib.md5(cl.clause_title.encode('utf-8')).hexdigest()[:6]}"
            if rc in seen_rule_codes:
                continue
            seen_rule_codes.add(rc)
            rule_drafts.append(
                {
                    "code": rc,
                    "title": cl.clause_title,
                    "rule_category": cl.clause_kind if cl.clause_kind in {"INCLUSION", "EXCLUSION", "BRANCH", "DEDUP"} else "INCLUSION",
                    "business_condition": sanitize_business_text(cl.business_requirement),
                    "business_action": "当前系统处理逻辑中尚未检索到该项规则的实际落地实现",
                    "business_rationale": "制度规范已明确要求，但目前仅停留在制度文件阶段，建议纳入后续开发排期。",
                    "reconcile_status": "UNREALIZED",
                    "evidence_ids": [],
                    "clause_ids": [cl.id],
                    "audience": cl.audience,
                }
            )

    # 3) 合成业务指标口径卡（从 CALIBER 条款 + 底层计算实证对齐）
    caliber_clauses = [c for c in d_clauses if c.clause_kind == "CALIBER"]
    caliber_drafts: List[Dict[str, Any]] = []
    seen_cal_codes = set()
    for cl in caliber_clauses:
        raw_title = cl.clause_title
        if "——" in raw_title:
            raw_title = raw_title.split("——", 1)[1]
        metric_name = re.sub(
            r"(公式与口径细则|公式与计算口径|口径管理|专项核验|的官方取数口径|字段的|报送映射要求|监管要求)$",
            "",
            raw_title,
        ).strip()
        for known in ("交易金额", "交易后余额", "跨境联名账户折算余额", "综合授信额度", "大额结售汇"):
            if known in cl.clause_title:
                metric_name = known
                break
        metric_name = sanitize_business_text(metric_name).strip('“”"\' ：:') or sanitize_business_text(cl.clause_title)
        if len(metric_name) > 28:
            metric_name = metric_name[:26].rstrip("，；： ") + "…"
        cal_code = f"cal-{hashlib.md5(metric_name.encode('utf-8')).hexdigest()[:8]}"
        if cal_code in seen_cal_codes:
            continue
        seen_cal_codes.add(cal_code)
        metric_tokens = set(tokenize_for_search(f"{metric_name} {cl.clause_title}"))
        matching_evs = [
            e
            for e in d_evs
            if metric_name in e.target_concept_hint
            or metric_name in e.calculation_summary
            or metric_name in e.source_summary
            or (metric_name in {"交易金额", "交易后余额"} and e.calculation_kind == "DIRECT")
            or (e.calculation_kind == "CONFLICT" and ("联名" in metric_name or "对手" in cl.clause_title or "大额结售汇" in metric_name))
            or bool(metric_tokens and (metric_tokens & set(tokenize_for_search(f"{e.target_concept_hint} {e.calculation_summary} {e.source_summary}"))))
        ]
        if not matching_evs and d_evs:
            matching_evs = list(d_evs)

        def _ev_caliber_priority(ev: LogicEvidenceModel) -> Tuple[int, int, int, int, int]:
            loc = ev.internal_locator_json or {}
            text_blob = f"{ev.target_concept_hint} {ev.calculation_summary} {ev.source_summary}"
            is_conflict_match = 1 if (
                ev.calculation_kind == "CONFLICT"
                and ("联名" in metric_name or "对手" in cl.clause_title or "对手" in cl.business_requirement or "分歧" in text_blob)
            ) else 0
            calc_rank = {"CONFLICT": 4, "AGGREGATE": 3, "CONDITIONAL": 2, "DIRECT": 1}.get(ev.calculation_kind, 0)
            token_hits = len(metric_tokens & set(tokenize_for_search(text_blob))) if metric_tokens else 0
            stage_role_calc = 1 if str(loc.get("stage_role") or "") == "CALCULATION" else 0
            ai_lead_score = 2 if (loc.get("ai_refined") and loc.get("stage_lead")) else (1 if loc.get("ai_refined") else 0)
            chunk_idx = int(loc.get("chunk_index") or 0)
            return (is_conflict_match, calc_rank, token_hits, stage_role_calc + ai_lead_score, chunk_idx)

        matching_evs.sort(key=_ev_caliber_priority, reverse=True)
        ev_for_cal = [e.id for e in matching_evs]
        is_conflict = any(e.calculation_kind == "CONFLICT" for e in matching_evs) and (
            "联名" in metric_name or "对手" in cl.clause_title or "对手" in cl.business_requirement
        )
        if not ev_for_cal:
            r_status = "UNREALIZED"
            formula_summary = "当前系统逻辑尚未包含该项指标的具体计算实现（仅制度材料提及）。"
            conflict_notes = ""
        elif is_conflict:
            r_status = "DIVERGENT"
            conflict_ev = next(e for e in matching_evs if e.calculation_kind == "CONFLICT")
            formula_summary = conflict_ev.calculation_summary
            conflict_notes = conflict_ev.calculation_summary
        else:
            r_status = "VERIFIED"
            if "交易后余额" in metric_name:
                formula_summary = "按交易发生完成瞬间的账户静态余额原值直接传出，不进行跨日累计或日均折算。"
            elif "交易金额" in metric_name:
                formula_summary = "按单笔实际发生金额原值直接传出，不做跨日汇总或四舍五入截断。"
            else:
                formula_summary = matching_evs[0].calculation_summary
            conflict_notes = ""

        domain_exclusions = [r["title"] for r in rule_drafts if r["rule_category"] == "EXCLUSION" and r.get("title")]
        if domain_exclusions:
            exclusion_notes = "当期自动排除以下情形：" + "；".join(dict.fromkeys(domain_exclusions[:3])) + "。"
        elif "应报未报" in domain_name or "大额与可疑外汇交易" in domain_name:
            exclusion_notes = "若属于应报未报待补报账户或已撤回/已报送记录，不计入当期常规报送结果。"
        else:
            exclusion_notes = "符合当期剔除规则或例外排除条件的业务记录不计入本口径统计。"

        if "交易后余额" in metric_name:
            distinction_notes = "重要区分：交易后余额是交易时点静态快照，严禁与日均余额混用。"
        elif "余额" in metric_name:
            distinction_notes = "请按当期真实核算时点与净额/全额口径区分使用，避免与日均余额或未扣减质押冻结前的毛额混用。"
        else:
            distinction_notes = "请勿将该口径与跨业务阶段汇总指标混用。"

        caliber_drafts.append(
            {
                "code": cal_code,
                "metric_name": metric_name,
                "business_definition": sanitize_business_text(cl.business_requirement),
                "formula_summary": formula_summary,
                "inclusion_scope": f"适用业务范围：{domain_name}（随当期主体圈定与有效状态规则一并生效）",
                "exclusion_notes": exclusion_notes,
                "distinction_notes": distinction_notes,
                "triggers": [metric_name, sanitize_business_text(cl.clause_title)],
                "reconcile_status": r_status,
                "conflict_notes": conflict_notes,
                "evidence_ids": ev_for_cal,
                "clause_ids": [cl.id],
                "audience": cl.audience,
            }
        )

    # 如果某领域属于尚未落地的专项监测，且没有 CALIBER 条款，则补充一条 UNREALIZED 口径核验卡以便对账总览清晰呈现
    if not d_evs and not caliber_clauses and d_clauses:
        metric_name = sanitize_business_text(d_clauses[0].clause_title)
        if len(metric_name) > 28:
            metric_name = metric_name[:26].rstrip("，；： ") + "…"
        cal_code = f"cal-{hashlib.md5(metric_name.encode('utf-8')).hexdigest()[:8]}"
        caliber_drafts.append(
            {
                "code": cal_code,
                "metric_name": metric_name,
                "business_definition": sanitize_business_text(d_clauses[0].business_requirement),
                "formula_summary": "制度材料明确提出了此专项核验要求，但当前已接入的系统逻辑尚未实现。",
                "inclusion_scope": domain_name,
                "exclusion_notes": "尚待业务与研发确认纳入排期后补齐实现。",
                "distinction_notes": "请勿对外宣传系统已具备该专项自动核验能力。",
                "triggers": [metric_name, domain_name],
                "reconcile_status": "UNREALIZED",
                "conflict_notes": "系统底层暂无对应实现支撑。",
                "evidence_ids": [],
                "clause_ids": [c.id for c in d_clauses],
                "audience": d_clauses[0].audience,
            }
        )

    brief_clauses = [
        {
            "title": sanitize_business_text(c.clause_title),
            "kind": c.clause_kind,
            "requirement": sanitize_business_text(c.business_requirement)[:300],
        }
        for c in d_clauses[:12]
    ]
    brief_evidences = [
        {
            "evidence_id": e.id,
            "target_concept": sanitize_business_text(e.target_concept_hint),
            "source_summary": sanitize_business_text(e.source_summary)[:240],
            "calculation_kind": e.calculation_kind,
            "calculation_summary": sanitize_business_text(e.calculation_summary)[:240],
            "dedup_grain": sanitize_business_text(e.dedup_grain_hint or "")[:160],
            "key_rules": _summarize_evidence_rules(e, limit=6),
        }
        for e in d_evs[:15]
    ]

    return {
        "domain_name": domain_name,
        "cap_code": cap_code,
        "coverage_status": coverage_status,
        "ev_ids": ev_ids,
        "cl_ids": cl_ids,
        "audience": audience,
        "capability": {
            "code": cap_code,
            "name": domain_name,
            "description": sanitize_business_text(description),
            "scope_summary": scope_summary or f"覆盖「{domain_name}」全部核心业务处理环节。",
            "audience": audience,
        },
        "rules": rule_drafts,
        "calibers": caliber_drafts,
        "brief_clauses": brief_clauses,
        "brief_evidences": brief_evidences,
    }


def _refine_domain_with_llm(
    draft: Dict[str, Any],
    *,
    llm: Any,
    bus: Any,
) -> Tuple[Dict[str, Any], bool]:
    """利用受控大模型对单一业务域进行代码实证 × 监管制度深度业务融合提炼。"""
    if llm is None:
        return draft, False
    from app.kernel.run import BudgetExceededError

    payload = {
        "domain_name": draft["domain_name"],
        "coverage_status": draft["coverage_status"],
        "allowed_evidence_ids": draft["ev_ids"],
        "regulatory_clauses": draft["brief_clauses"],
        "logic_evidences": draft["brief_evidences"],
        "draft_capability": draft["capability"],
        "draft_rules": [
            {
                "code": r["code"],
                "title": r["title"],
                "rule_category": r["rule_category"],
                "business_condition": r["business_condition"],
                "business_action": r["business_action"],
                "business_rationale": r["business_rationale"],
                "evidence_ids": r["evidence_ids"],
            }
            for r in draft["rules"][:18]
        ],
        "draft_calibers": [
            {
                "code": c["code"],
                "metric_name": c["metric_name"],
                "business_definition": c["business_definition"],
                "formula_summary": c["formula_summary"],
                "distinction_notes": c["distinction_notes"],
                "conflict_notes": c["conflict_notes"],
                "evidence_ids": c["evidence_ids"],
            }
            for c in draft["calibers"][:8]
        ],
    }
    try:
        args, _trace = call_llm_validated(
            llm,
            "synthesize_domain_assets",
            (
                "结合监管制度条款与系统底层实证，在不编造未实证规则、不丢失原规则 code、"
                "且严禁出现任何物理表名、字段名（大小写）、文件名或 SQL 关键词的前提下，将本业务域的概述、"
                "适用边界、核心规则条件/结果/业务原因及指标口径提炼融合为清晰自然的中文业务表述。"
                "请逐条核对 logic_evidences 与 draft_rules：凡系统实证已明确实现的扣减公式、审批/复核门禁、状态排除或覆盖机制，"
                "必须在融合描述与口径公式中如实肯定为“已落地”并写清具体业务口径，严禁把已实现逻辑误判为尚未落地；"
                "仅在确实存在多口径分歧或制度未落地缺口时如实点明。"
            ),
            payload,
            backoff=(0.1, 0.2),
            bus=bus,
        )
    except BudgetExceededError:
        raise
    except (WorkflowError, RuntimeError):
        return draft, False

    cap = draft["capability"]
    if str(args.get("description") or "").strip():
        cap["description"] = sanitize_business_text(str(args["description"]).strip()) or cap["description"]
    if str(args.get("scope_summary") or "").strip():
        cap["scope_summary"] = sanitize_business_text(str(args["scope_summary"]).strip()) or cap["scope_summary"]

    allowed_ev_set = set(draft["ev_ids"])
    ai_rules = {
        str(item.get("code")): item
        for item in (args.get("rules") or [])
        if isinstance(item, dict) and item.get("code")
    }
    for rule in draft["rules"]:
        refined = ai_rules.get(rule["code"])
        if not refined:
            continue
        for field in ("title", "business_condition", "business_action", "business_rationale"):
            val = sanitize_business_text(str(refined.get(field) or "").strip())
            if val:
                rule[field] = val
        if isinstance(refined.get("evidence_ids"), list) and rule["evidence_ids"]:
            filtered = [eid for eid in refined["evidence_ids"] if eid in allowed_ev_set]
            if filtered:
                rule["evidence_ids"] = filtered

    ai_calibers = {
        str(item.get("code")): item
        for item in (args.get("calibers") or [])
        if isinstance(item, dict) and item.get("code")
    }
    for cal in draft["calibers"]:
        refined = ai_calibers.get(cal["code"])
        if not refined:
            continue
        for field in ("metric_name", "business_definition", "formula_summary", "distinction_notes", "conflict_notes"):
            val = sanitize_business_text(str(refined.get(field) or "").strip())
            if val:
                cal[field] = val
        if isinstance(refined.get("evidence_ids"), list) and cal["evidence_ids"]:
            filtered = [eid for eid in refined["evidence_ids"] if eid in allowed_ev_set]
            if filtered:
                cal["evidence_ids"] = filtered

    return draft, True


_DOMAIN_PREFIX_RE = re.compile(
    r"^(?:1104|EAST(?:\s*5\.0|5|4)?|SAFE|FAL|BOP|PLUS|SAC|一表通|G\d{2}|S\d{2}|GF\d{2}|SF\d{2})[-－—·:：\s]*",
    re.IGNORECASE,
)


def _normalize_domain_core(name: str) -> str:
    """去除监管体系前缀、括号后缀及空白，提取业务域核心语义名称用于跨源精准对齐。"""
    core = re.sub(r"[（(].*$", "", str(name or "")).strip()
    while True:
        stripped = _DOMAIN_PREFIX_RE.sub("", core).strip()
        if stripped == core:
            break
        core = stripped
    return re.sub(r"\s+", "", core).strip()


def _align_evidence_domain_to_clauses(
    ev: LogicEvidenceModel,
    doc_domains: List[str],
    doc_domain_regimes: Dict[str, str],
) -> str:
    """在严守监管体系同源与高相似度前提下，将代码实证域安全对齐到对应监管制度域，防止同类微差拆成假未落地。"""
    ev_domain = ev.business_domain_hint
    if ev_domain in doc_domains:
        return ev_domain

    ev_loc = ev.internal_locator_json or {}
    ev_regime = str(ev_loc.get("regime") or "").strip() or infer_regulatory_regime(
        ev_domain,
        ev.target_concept_hint,
        ev.source_summary,
    )
    ev_core = _normalize_domain_core(ev_domain)
    ev_tokens = set(tokenize_for_search(ev_core or ev_domain))

    best_domain = ev_domain
    best_score = 0.0

    for doc_domain in doc_domains:
        doc_regime = doc_domain_regimes.get(doc_domain) or "通用监管"
        if ev_regime != "通用监管" and doc_regime != "通用监管" and ev_regime != doc_regime:
            continue

        doc_core = _normalize_domain_core(doc_domain)
        score = 0.0
        if len(ev_core) >= 4 and len(doc_core) >= 4:
            if ev_core == doc_core:
                score = 1.0
            elif ev_core in doc_core or doc_core in ev_core:
                ratio = min(len(ev_core), len(doc_core)) / max(len(ev_core), len(doc_core))
                if ratio >= 0.55:
                    score = 0.90

        if score < 0.50 and ev_tokens:
            doc_tokens = set(tokenize_for_search(doc_core or doc_domain))
            if doc_tokens:
                overlap = len(ev_tokens & doc_tokens)
                if overlap >= 3:
                    jaccard = overlap / len(ev_tokens | doc_tokens)
                    contain = overlap / min(len(ev_tokens), len(doc_tokens))
                    if jaccard >= 0.50 or (contain >= 0.72 and jaccard >= 0.35):
                        score = max(score, 0.50 + 0.30 * jaccard)

        if score > best_score:
            best_score = score
            best_domain = doc_domain

    return best_domain if best_score >= 0.50 else ev_domain


def synthesize_business_assets(
    session: Session,
    *,
    settings: Optional[Settings] = None,
    llm: Any = None,
) -> Dict[str, Any]:
    """将底层代码实证与监管制度条款自动碰撞对齐，生成纯业务语言四大资产层并落哈希链账本。"""
    active_settings = get_effective_settings(session, settings or get_settings())
    active_llm = _resolve_optional_llm(session, active_settings, llm)
    budget = get_budget(session)
    configure_llm_concurrency(int(budget.get("max_llm_concurrency", 4)))
    bus = build_run_bus(budget)

    run: RunModel = kernel_create_run(session, "dc_asset_synthesis", initial_node="created", bus=bus)
    clauses = session.query(RegulatoryClauseModel).all()
    evidences = session.query(LogicEvidenceModel).all()

    append_event(
        session,
        run,
        "SOURCES_PARSED",
        "parse_sources",
        {"clause_count": len(clauses), "evidence_count": len(evidences)},
        bus=bus,
    )

    doc_domains: List[str] = []
    doc_clauses_by_domain: Dict[str, List[RegulatoryClauseModel]] = {}
    for cl in clauses:
        if cl.business_domain not in doc_clauses_by_domain:
            doc_domains.append(cl.business_domain)
            doc_clauses_by_domain[cl.business_domain] = []
        doc_clauses_by_domain[cl.business_domain].append(cl)

    doc_domain_regimes: Dict[str, str] = {}
    for d in doc_domains:
        clause_regimes = [
            str((c.source_locator_json or {}).get("regime") or "").strip()
            for c in doc_clauses_by_domain[d]
            if (c.source_locator_json or {}).get("regime")
        ]
        if clause_regimes and clause_regimes[0] != "通用监管":
            doc_domain_regimes[d] = clause_regimes[0]
        else:
            doc_domain_regimes[d] = infer_regulatory_regime(
                d,
                " ".join(c.clause_title for c in doc_clauses_by_domain[d][:3]),
            )

    domains_order: List[str] = list(doc_domains)
    domain_clauses: Dict[str, List[RegulatoryClauseModel]] = {
        d: list(doc_clauses_by_domain[d]) for d in doc_domains
    }
    domain_evidences: Dict[str, List[LogicEvidenceModel]] = {d: [] for d in doc_domains}

    for ev in evidences:
        target_domain = _align_evidence_domain_to_clauses(ev, doc_domains, doc_domain_regimes)
        if target_domain not in domains_order:
            domains_order.append(target_domain)
            domain_clauses[target_domain] = []
            domain_evidences[target_domain] = []
        domain_evidences[target_domain].append(ev)

    append_event(
        session,
        run,
        "EVIDENCE_ALIGNED",
        "align_evidence",
        {
            "domains": domains_order,
            "covered_domains": [d for d in domains_order if domain_evidences[d]],
            "unrealized_domains": [d for d in domains_order if not domain_evidences[d]],
        },
        bus=bus,
    )

    drafts = [
        _build_domain_draft(domain_name, domain_clauses[domain_name], domain_evidences[domain_name])
        for domain_name in domains_order
    ]

    ai_enriched_domains = 0
    if active_llm is not None and drafts:
        workers = max(1, min(6, int(budget.get("max_llm_concurrency", 4))))
        with llm_execution_scope(bus=bus):
            if len(drafts) == 1:
                drafts[0], ok = _refine_domain_with_llm(drafts[0], llm=active_llm, bus=bus)
                if ok:
                    ai_enriched_domains = 1
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [
                        (idx, submit_with_context(pool, _refine_domain_with_llm, d, llm=active_llm, bus=bus))
                        for idx, d in enumerate(drafts)
                    ]
                    for idx, fut in futures:
                        drafts[idx], ok = fut.result()
                        if ok:
                            ai_enriched_domains += 1

    cap_total = 0
    rule_total = 0
    caliber_total = 0

    for draft in drafts:
        cap_payload = draft["capability"]
        cap_row = _upsert_capability(
            session,
            code=draft["cap_code"],
            name=cap_payload["name"],
            description=cap_payload["description"],
            scope_summary=cap_payload["scope_summary"],
            audience=cap_payload.get("audience", draft["audience"]),
            coverage_status=draft["coverage_status"],
            evidence_ids=draft["ev_ids"],
            clause_ids=draft["cl_ids"],
            rule_count=0,
            caliber_count=0,
            confirmed_count=0,
            bus=bus,
        )
        cap_total += 1
        cap_rule_count = 0
        cap_caliber_count = 0

        for rule_draft in draft["rules"]:
            _upsert_rule(
                session,
                capability_id=cap_row.id,
                bus=bus,
                **rule_draft,
            )
            cap_rule_count += 1
            rule_total += 1

        for cal_draft in draft["calibers"]:
            _upsert_caliber(
                session,
                capability_id=cap_row.id,
                bus=bus,
                **cal_draft,
            )
            cap_caliber_count += 1
            caliber_total += 1

        confirmed_rules = (
            session.query(BusinessRuleAssetModel)
            .filter(BusinessRuleAssetModel.capability_id == cap_row.id, BusinessRuleAssetModel.status == "CONFIRMED")
            .count()
        )
        confirmed_cals = (
            session.query(BusinessCaliberAssetModel)
            .filter(BusinessCaliberAssetModel.capability_id == cap_row.id, BusinessCaliberAssetModel.status == "CONFIRMED")
            .count()
        )
        cap_row.rule_count = cap_rule_count
        cap_row.caliber_count = cap_caliber_count
        cap_row.confirmed_count = confirmed_rules + confirmed_cals
        session.flush()

    summary = {
        "run_id": run.id,
        "capability_count": cap_total,
        "rule_count": rule_total,
        "caliber_count": caliber_total,
        "ai_enabled": active_llm is not None,
        "llm_model": _resolve_llm_model_name(active_llm, active_settings),
        "ai_enriched_domains": ai_enriched_domains,
    }
    append_event(
        session,
        run,
        "ASSETS_SYNTHESIZED",
        "synthesize_assets",
        {"summary": summary},
        bus=bus,
    )
    session.commit()
    return summary


def load_builtin_sample_pack(
    session: Session,
    *,
    settings: Optional[Settings] = None,
    include_real_sac: bool = False,
    sql_explorer_dir: Optional[str] = None,
    llm: Any = None,
) -> Dict[str, Any]:
    """一键载入金融监管制度 + 逻辑代码双源样例并自动合成业务资产层。"""
    if include_real_sac:
        return load_real_sac_corpus(
            session,
            sql_explorer_dir=sql_explorer_dir,
            settings=settings,
            llm=llm,
        )
    active_settings = get_effective_settings(session, settings or get_settings())
    items = [
        (title, "REGULATORY_DOC", "md", raw)
        for title, raw in BUILTIN_REGULATORY_DOCS
    ] + [
        (title, "CODE_LOGIC", "sql", raw)
        for title, raw in BUILTIN_CODE_SOURCES
    ]
    _ingest_materials_batch(session, items, settings=active_settings, llm=llm)
    session.commit()
    return synthesize_business_assets(session, settings=active_settings, llm=llm)


def load_real_sac_corpus(
    session: Session,
    *,
    sql_explorer_dir: Optional[str] = None,
    settings: Optional[Settings] = None,
    llm: Any = None,
) -> Dict[str, Any]:
    """载入 sql_explorer 真实 25 份 SAC 脚本 + 真实 SAC 监管/口径/术语文档并全量合成业务资产。"""
    active_settings = get_effective_settings(session, settings or get_settings())
    reg_docs, code_sources = collect_real_sac_materials(sql_explorer_dir)
    items = [
        (title, "REGULATORY_DOC", "md", raw)
        for title, raw in reg_docs
    ] + [
        (title, "CODE_LOGIC", "sql", raw)
        for title, raw in code_sources
    ]
    _ingest_materials_batch(session, items, settings=active_settings, llm=llm)
    session.commit()
    return synthesize_business_assets(session, settings=active_settings, llm=llm)
