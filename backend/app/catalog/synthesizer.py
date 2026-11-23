"""双源接入落库与「代码 × 监管材料」纯业务资产融合合成引擎（资产层 Layer 4）。"""

import hashlib
import re
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infra.blob_store import LocalBlobStore
from app.infra.config import Settings, get_effective_settings, get_settings
from app.infra.db_tables import (
    BusinessCaliberAssetModel,
    BusinessCapabilityModel,
    BusinessRuleAssetModel,
    LogicEvidenceModel,
    RegulatoryClauseModel,
    RunModel,
    SourceMaterialModel,
)
from app.infra.llm import RealDesignLlm, call_llm_validated
from app.kernel.event_log import append_event
from app.kernel.run import create_run as kernel_create_run
from app.ingestion.builtin_pack import (
    BUILTIN_CODE_SOURCES,
    BUILTIN_REGULATORY_DOCS,
    collect_real_sac_materials,
)
from app.ingestion.code_parser import parse_code_logic
from app.ingestion.doc_parser import parse_regulatory_document
from app.policy.dc_bus import default_bus, detect_technical_jargon, sanitize_business_text


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


def ingest_material(
    session: Session,
    *,
    title: str,
    material_kind: str,
    file_type: str,
    content: bytes,
    settings: Optional[Settings] = None,
    llm: Any = None,
) -> SourceMaterialModel:
    """并发安全地摄入监管制度文档或代码逻辑源，并提取条款或隐身底层实证。"""
    if material_kind not in {"REGULATORY_DOC", "CODE_LOGIC"}:
        raise ValueError(f"Unsupported material_kind: {material_kind}")
    active_settings = settings or get_settings()
    digest = hashlib.sha256(content).hexdigest()
    blob_uri = LocalBlobStore(active_settings.blob_dir).put(content)

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

    if material_kind == "REGULATORY_DOC":
        clauses = parse_regulatory_document(title, content)
        for c in clauses:
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
        material.item_count = len(clauses)
        material.summary = f"已从制度文档提取 {len(clauses)} 条业务规范与口径条款。"
    else:
        active_llm = _resolve_optional_llm(session, active_settings, llm)
        evidences = parse_code_logic(
            title,
            content,
            llm=active_llm,
            bus=default_bus() if active_llm is not None else None,
        )
        stored = 0
        for ev in evidences:
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
        material.summary = f"已静默提取 {stored} 组底层逻辑实证（物理对象已隔离封存，不对外展示）。"

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


def _polish_with_optional_llm(
    tool_name: str,
    raw_payload: Dict[str, Any],
    allowed_evidence_ids: List[str],
    *,
    llm: Any,
    bus,
) -> Dict[str, Any]:
    if llm is None:
        return raw_payload
    args, _trace = call_llm_validated(
        llm,
        tool_name,
        "把输入业务内容提炼为更加凝练自然的中文业务表达，严禁出现任何代码、表名、字段名或 SQL 关键词。",
        {**raw_payload, "allowed_evidence_ids": allowed_evidence_ids},
        backoff=(0.1, 0.2),
        bus=bus,
    )
    return {**raw_payload, **args}


def synthesize_business_assets(
    session: Session,
    *,
    settings: Optional[Settings] = None,
    llm: Any = None,
) -> Dict[str, Any]:
    """将底层代码实证与监管制度条款自动碰撞对齐，生成纯业务语言四大资产层并落哈希链账本。"""
    bus = default_bus()
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

    domains_order: List[str] = []
    for cl in clauses:
        if cl.business_domain not in domains_order:
            domains_order.append(cl.business_domain)
    for ev in evidences:
        if ev.business_domain_hint not in domains_order:
            domains_order.append(ev.business_domain_hint)

    domain_clauses: Dict[str, List[RegulatoryClauseModel]] = {d: [] for d in domains_order}
    domain_evidences: Dict[str, List[LogicEvidenceModel]] = {d: [] for d in domains_order}
    for cl in clauses:
        domain_clauses[cl.business_domain].append(cl)
    for ev in evidences:
        domain_evidences[ev.business_domain_hint].append(ev)

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

    cap_total = 0
    rule_total = 0
    caliber_total = 0

    for idx, domain_name in enumerate(domains_order):
        d_clauses = domain_clauses[domain_name]
        d_evs = domain_evidences[domain_name]
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

        cap_payload = _polish_with_optional_llm(
            "synthesize_business_capability",
            {
                "code": cap_code,
                "name": domain_name,
                "description": sanitize_business_text(description),
                "scope_summary": scope_summary or f"覆盖「{domain_name}」全部核心业务处理环节。",
                "audience": audience,
            },
            ev_ids,
            llm=llm,
            bus=bus,
        )
        cap_row = _upsert_capability(
            session,
            code=cap_code,
            name=cap_payload["name"],
            description=cap_payload["description"],
            scope_summary=cap_payload["scope_summary"],
            audience=cap_payload.get("audience", audience),
            coverage_status=coverage_status,
            evidence_ids=ev_ids,
            clause_ids=cl_ids,
            rule_count=0,
            caliber_count=0,
            confirmed_count=0,
            bus=bus,
        )
        cap_total += 1
        cap_rule_count = 0
        cap_caliber_count = 0

        seen_rule_codes = set()
        # 1) 从底层实证提炼已实现/冲突规则
        for ev in d_evs:
            for item in ev.inclusion_predicates_json or []:
                rc_slug = item["rule_code"].lower().replace("_", "-")
                rc = f"rule-{cap_code[4:]}-{rc_slug}"
                if rc in seen_rule_codes:
                    continue
                seen_rule_codes.add(rc)
                matched_clauses = _match_relevant_clauses(
                    f"{item['title']} {item['condition']}", d_clauses, {"INCLUSION", "CAPABILITY"}
                )
                _upsert_rule(
                    session,
                    capability_id=cap_row.id,
                    code=rc,
                    title=item["title"],
                    rule_category="INCLUSION",
                    business_condition=item["condition"],
                    business_action="满足该条件则纳入当期正式业务处理与报送范围",
                    business_rationale=item["rationale"],
                    reconcile_status="VERIFIED" if matched_clauses else "CONSISTENT",
                    evidence_ids=[ev.id],
                    clause_ids=matched_clauses,
                    audience=audience,
                    bus=bus,
                )
                cap_rule_count += 1
                rule_total += 1

            for item in ev.exclusion_predicates_json or []:
                rc_slug = item["rule_code"].lower().replace("_", "-")
                rc = f"rule-{cap_code[4:]}-{rc_slug}"
                if rc in seen_rule_codes:
                    continue
                seen_rule_codes.add(rc)
                matched_clauses = _match_relevant_clauses(
                    f"{item['title']} {item['condition']}", d_clauses, {"EXCLUSION"}
                )
                _upsert_rule(
                    session,
                    capability_id=cap_row.id,
                    code=rc,
                    title=item["title"],
                    rule_category="EXCLUSION",
                    business_condition=item["condition"],
                    business_action="自动剔除出当期常规报送结果（必要时单列进入风险待办清单）",
                    business_rationale=item["rationale"],
                    reconcile_status="VERIFIED" if matched_clauses else "CONSISTENT",
                    evidence_ids=[ev.id],
                    clause_ids=matched_clauses,
                    audience="业务 / 监管报送 / 合规",
                    bus=bus,
                )
                cap_rule_count += 1
                rule_total += 1

            for b_idx, item in enumerate(ev.branch_cases_json or []):
                rc = f"rule-{cap_code[4:]}-branch-{hashlib.md5(item['title'].encode('utf-8')).hexdigest()[:6]}"
                if rc in seen_rule_codes:
                    continue
                seen_rule_codes.add(rc)
                is_div = "分歧" in item["title"] or ev.calculation_kind == "CONFLICT"
                matched_clauses = _match_relevant_clauses(
                    f"{item['title']} {item['condition']}", d_clauses, {"BRANCH", "CALIBER"}
                )
                _upsert_rule(
                    session,
                    capability_id=cap_row.id,
                    code=rc,
                    title=item["title"],
                    rule_category="BRANCH",
                    business_condition=item["condition"],
                    business_action=item["action"],
                    business_rationale="按业务主体类别或账户属性自动进入差异化处理分支。",
                    reconcile_status="DIVERGENT" if is_div else ("VERIFIED" if matched_clauses else "CONSISTENT"),
                    evidence_ids=[ev.id],
                    clause_ids=matched_clauses,
                    audience=audience,
                    bus=bus,
                )
                cap_rule_count += 1
                rule_total += 1

            if ev.dedup_grain_hint:
                rc = f"rule-{cap_code[4:]}-dedup-{hashlib.md5(ev.dedup_grain_hint.encode('utf-8')).hexdigest()[:6]}"
                if rc not in seen_rule_codes:
                    seen_rule_codes.add(rc)
                    matched_clauses = _match_relevant_clauses(
                        ev.dedup_grain_hint, d_clauses, {"INCLUSION", "CAPABILITY"}
                    )
                    _upsert_rule(
                        session,
                        capability_id=cap_row.id,
                        code=rc,
                        title="唯一记录防重保留规则",
                        rule_category="DEDUP",
                        business_condition="同一客户、账户或交易在系统加工过程中出现多条状态记录时",
                        business_action=ev.dedup_grain_hint,
                        business_rationale="保障报送金额与交易笔数准确唯一，防止同一业务被重复计数。",
                        reconcile_status="VERIFIED" if matched_clauses else "CONSISTENT",
                        evidence_ids=[ev.id],
                        clause_ids=matched_clauses,
                        audience=audience,
                        bus=bus,
                    )
                    cap_rule_count += 1
                    rule_total += 1

        # 2) 制度里有要求但当前代码尚未包含的领域：落 UNREALIZED 规则提示差距
        if not d_evs:
            for cl in d_clauses:
                rc = f"rule-{cap_code[4:]}-unreal-{hashlib.md5(cl.clause_title.encode('utf-8')).hexdigest()[:6]}"
                if rc in seen_rule_codes:
                    continue
                seen_rule_codes.add(rc)
                _upsert_rule(
                    session,
                    capability_id=cap_row.id,
                    code=rc,
                    title=cl.clause_title,
                    rule_category=cl.clause_kind if cl.clause_kind in {"INCLUSION", "EXCLUSION", "BRANCH", "DEDUP"} else "INCLUSION",
                    business_condition=sanitize_business_text(cl.business_requirement),
                    business_action="当前系统处理逻辑中尚未检索到该项规则的实际落地实现",
                    business_rationale="制度规范已明确要求，但目前仅停留在制度文件阶段，建议纳入后续开发排期。",
                    reconcile_status="UNREALIZED",
                    evidence_ids=[],
                    clause_ids=[cl.id],
                    audience=cl.audience,
                    bus=bus,
                )
                cap_rule_count += 1
                rule_total += 1

        # 3) 合成业务指标口径卡（从 CALIBER 条款 + 底层计算实证对齐）
        caliber_clauses = [c for c in d_clauses if c.clause_kind == "CALIBER"]
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
            matching_evs = [
                e
                for e in d_evs
                if metric_name in e.target_concept_hint
                or metric_name in e.calculation_summary
                or (metric_name in {"交易金额", "交易后余额"} and e.calculation_kind == "DIRECT")
                or (e.calculation_kind == "CONFLICT" and ("联名" in metric_name or "对手" in cl.clause_title or "大额结售汇" in metric_name))
            ]
            if not matching_evs and d_evs:
                matching_evs = [d_evs[0]]
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

            _upsert_caliber(
                session,
                capability_id=cap_row.id,
                code=cal_code,
                metric_name=metric_name,
                business_definition=sanitize_business_text(cl.business_requirement),
                formula_summary=formula_summary,
                inclusion_scope=f"适用业务范围：{domain_name}（随当期主体圈定与有效状态规则一并生效）",
                exclusion_notes="若属于应报未报待补报账户或已撤回/已报送记录，不计入当期常规报送结果。",
                distinction_notes=(
                    "重要区分：交易后余额是交易时点静态快照，严禁与日均余额混用。"
                    if "余额" in metric_name
                    else "请勿将该口径与跨业务阶段汇总指标混用。"
                ),
                triggers=[metric_name, sanitize_business_text(cl.clause_title)],
                reconcile_status=r_status,
                conflict_notes=conflict_notes,
                evidence_ids=ev_for_cal,
                clause_ids=[cl.id],
                audience=cl.audience,
                bus=bus,
            )
            cap_caliber_count += 1
            caliber_total += 1

        # 如果某领域属于尚未落地的专项监测，且没有 CALIBER 条款，则补充一条 UNREALIZED 口径核验卡以便对账总览清晰呈现
        if not d_evs and not caliber_clauses and d_clauses:
            metric_name = sanitize_business_text(d_clauses[0].clause_title)
            if len(metric_name) > 28:
                metric_name = metric_name[:26].rstrip("，；： ") + "…"
            cal_code = f"cal-{hashlib.md5(metric_name.encode('utf-8')).hexdigest()[:8]}"
            _upsert_caliber(
                session,
                capability_id=cap_row.id,
                code=cal_code,
                metric_name=metric_name,
                business_definition=sanitize_business_text(d_clauses[0].business_requirement),
                formula_summary="制度材料明确提出了此专项核验要求，但当前已接入的系统逻辑尚未实现。",
                inclusion_scope=domain_name,
                exclusion_notes="尚待业务与研发确认纳入排期后补齐实现。",
                distinction_notes="请勿对外宣传系统已具备该专项自动核验能力。",
                triggers=[metric_name, domain_name],
                reconcile_status="UNREALIZED",
                conflict_notes="系统底层暂无对应实现支撑。",
                evidence_ids=[],
                clause_ids=[c.id for c in d_clauses],
                audience=d_clauses[0].audience,
                bus=bus,
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
) -> Dict[str, Any]:
    """一键载入金融监管制度 + 逻辑代码双源样例并自动合成业务资产层。"""
    if include_real_sac:
        return load_real_sac_corpus(
            session,
            sql_explorer_dir=sql_explorer_dir,
            settings=settings,
        )
    active_settings = settings or get_settings()
    for title, raw in BUILTIN_REGULATORY_DOCS:
        ingest_material(
            session,
            title=title,
            material_kind="REGULATORY_DOC",
            file_type="md",
            content=raw,
            settings=active_settings,
        )
    for title, raw in BUILTIN_CODE_SOURCES:
        ingest_material(
            session,
            title=title,
            material_kind="CODE_LOGIC",
            file_type="sql",
            content=raw,
            settings=active_settings,
        )
    session.commit()
    return synthesize_business_assets(session, settings=active_settings)


def load_real_sac_corpus(
    session: Session,
    *,
    sql_explorer_dir: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """载入 sql_explorer 真实 25 份 SAC 脚本 + 真实 SAC 监管/口径/术语文档并全量合成业务资产。"""
    active_settings = settings or get_settings()
    reg_docs, code_sources = collect_real_sac_materials(sql_explorer_dir)
    for title, raw in reg_docs:
        ingest_material(
            session,
            title=title,
            material_kind="REGULATORY_DOC",
            file_type="md",
            content=raw,
            settings=active_settings,
        )
    for title, raw in code_sources:
        ingest_material(
            session,
            title=title,
            material_kind="CODE_LOGIC",
            file_type="sql",
            content=raw,
            settings=active_settings,
        )
    session.commit()
    return synthesize_business_assets(session, settings=active_settings)
