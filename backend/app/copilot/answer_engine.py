"""纯业务智能问答编排引擎（Copilot 层 Layer 5）。

职责：
1. 接收业务人员的自然语言提问，确定性规划并调用 7 个纯业务工具收集实证与资产；
2. 触发 `disclose=True` 时把风险与未落地说明原样前置披露，严禁掩盖；
3. 经过 `default_bus()`（零代码黑话门禁 + 实证引用白名单门禁）校验并留存会话账本。
"""

from datetime import datetime
import json
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy.orm import Session

from app.catalog.views import list_capabilities
from app.copilot.business_tools import BUSINESS_TOOL_SCHEMAS, execute_business_tool
from app.copilot.disclosure import build_disclosure
from app.infra.config import Settings, get_budget, get_effective_settings, get_settings
from app.infra.db_tables import CopilotMessageModel, CopilotSessionModel
from app.infra.errors import WorkflowError
from app.infra.taxonomy import FINANCIAL_SUBJECT_THEMES, normalize_financial_theme
from app.infra.llm import (
    RealDesignLlm,
    call_llm_validated,
    configure_llm_concurrency,
    llm_execution_scope,
)
from app.kernel.event_log import _SessionRLock, bind_session_lock
from app.policy.dc_bus import build_run_bus, default_bus, detect_technical_jargon, sanitize_business_text

_SESSION_LOCKS: Dict[str, _SessionRLock] = {}


def _get_copilot_lock(session_id: str) -> _SessionRLock:
    lock = _SESSION_LOCKS.get(session_id)
    if lock is None:
        lock = _SessionRLock()
        _SESSION_LOCKS.setdefault(session_id, lock)
        lock = _SESSION_LOCKS[session_id]
    return lock


def _route_tools_for_query(query: str, role_lens: str) -> List[Tuple[str, Dict[str, Any]]]:
    q = (query or "").strip()
    plan: List[Tuple[str, Dict[str, Any]]] = []
    if any(k in q for k in ("包含", "哪些业务", "有什么业务", "盘点", "全景", "涵盖")):
        plan.append(("list_covered_businesses", {}))
    if any(k in q for k in ("还没", "未包含", "差距", "缺口", "未落地", "分歧", "冲突", "不一致", "对照")):
        plan.append(("check_regulatory_coverage_gap", {}))
    if any(k in q for k in ("余额", "交易金额", "口径", "怎么算", "联名", "折算", "公式", "资金来源", "资金使用", "结售汇", "门槛", "授信额度", "交易对手")):
        plan.append(("get_business_caliber_detail", {"metric": q}))
    if any(
        k in q
        for k in (
            "纳入", "剔除", "排除", "漏报", "应报未报", "没报", "分支", "规则", "分户账", "条件",
            "对公", "捞回", "个体工商户", "迁移", "机构信息", "授信", "测试", "结汇", "购汇",
            "境外", "境内外", "港澳", "信用卡", "怎么处理", "为什么",
        )
    ):
        plan.append(("get_business_scope_and_rules", {"topic": q}))
    if any(k in q for k in ("岗位", "我关心", "合规角色", "我的视角")):
        plan.append(("find_rules_by_business_role", {"role": role_lens}))
    if not plan:
        plan.append(("list_covered_businesses", {}))
        plan.append(("resolve_business_concept", {"concept": q}))
    return plan


def _deterministic_business_narrative(
    query: str,
    tool_outputs: List[Dict[str, Any]],
    backing_verdicts: List[Dict[str, Any]],
) -> str:
    sections: List[str] = []
    q = query or ""

    for out in tool_outputs:
        t = out.get("tool")
        if t == "list_covered_businesses":
            covered = out.get("covered_businesses") or []
            unrealized = out.get("unrealized_businesses") or []
            if covered:
                lines = [
                    f"截至目前，我们的系统业务逻辑已经正式包含 **{len(covered)} 项核心业务板块**（全部经过底层真实处理逻辑核实，而非仅停留在纸面文档）："
                ]
                for idx, c in enumerate(covered, start=1):
                    status_label = "存在多套口径分歧" if c["coverage_status"] == "PARTIAL" else "逻辑已完整覆盖"
                    lines.append(
                        f"{idx}. **{c['name']}**（{status_label}｜共提炼 {c['rule_count']} 条业务规则、{c['caliber_count']} 项指标口径）：{c['description']} 适用与边界：{c['scope_summary']}"
                    )
                if unrealized:
                    names = "、".join(u["name"] for u in unrealized)
                    lines.append(f"\n此外，制度材料中另有 **{len(unrealized)} 项要求尚未纳入系统逻辑**：{names}。")
                sections.append("\n".join(lines))

        elif t == "get_business_scope_and_rules":
            rules = out.get("rules") or []
            if rules:
                cat_labels = {
                    "INCLUSION": "纳入范围",
                    "EXCLUSION": "自动剔除 / 风险隔离",
                    "BRANCH": "分类分支处理",
                    "DEDUP": "防重复保留",
                }
                lines = ["围绕您咨询的业务处理规则，当前系统逻辑与制度对齐后的口径如下："]
                for r in rules[:6]:
                    tag = cat_labels.get(r["rule_category"], "业务规则")
                    lines.append(
                        f"- **【{tag}】{r['title']}**（所属业务：{r['capability_name']}）：\n"
                        f"  - 当前逻辑实际执行：{r['business_condition']}\n"
                        f"  - 对应业务解释与结果：{r['business_action']}\n"
                        f"  - 业务逻辑成因：{r['business_rationale']}"
                    )
                sections.append("\n".join(lines))

        elif t == "get_business_caliber_detail":
            cals = out.get("calibers") or []
            if cals:
                lines = ["相关业务指标的计算与统计口径说明如下："]
                for c in cals[:4]:
                    extra = f"\n  - ⚠️ 内部多口径分歧提示：{c['conflict_notes']}" if c.get("conflict_notes") else ""
                    regime_tag = f"｜{c['regime']}" if c.get("regime") else ""
                    lines.append(
                        f"- **{c['metric_name']}**（所属业务：{c['capability_name']}{regime_tag}）：{c['business_definition']}\n"
                        f"  - 实际处理算法：{c['formula_summary']}\n"
                        f"  - 适用与防混淆说明：{c['distinction_notes']}{extra}"
                    )
                sections.append("\n".join(lines))

        elif t == "check_regulatory_coverage_gap":
            summary = out.get("summary") or {}
            div_items = out.get("divergent_items") or []
            un_items = out.get("unrealized_items") or []
            lines = [
                f"当前制度材料与系统底层逻辑的全面对账结果为："
                f"已验证一致 **{summary.get('VERIFIED', 0) + summary.get('CONSISTENT', 0)} 项**、"
                f"存在内部多套口径分歧 **{len(div_items)} 项**、"
                f"仅制度提及但系统尚未包含 **{len(un_items)} 项**。"
            ]
            if div_items:
                lines.append("• **系统内存在多套并行处理路径的口径差异**：")
                for d in div_items:
                    lines.append(f"  - {d['name']}：{d['summary']}")
            if un_items:
                lines.append("• **系统逻辑暂未包含的制度要求**：")
                for u in un_items:
                    lines.append(f"  - {u['name']}：{u['summary']}")
            sections.append("\n".join(lines))

        elif t == "find_rules_by_business_role":
            rules = out.get("rules") or []
            cals = out.get("calibers") or []
            lines = [f"从**{out.get('role', '业务')}**视角筛选，您最需要关注的业务逻辑共有 {len(rules)} 条规则和 {len(cals)} 项指标口径："]
            for r in rules[:4]:
                lines.append(f"- 【规则】{r['title']}：{r['business_action']}")
            for c in cals[:3]:
                lines.append(f"- 【口径】{c['metric_name']}：{c['formula_summary']}")
            sections.append("\n".join(lines))

        elif t == "resolve_business_concept":
            caps = out.get("capabilities") or []
            rules = out.get("rules") or []
            cals = out.get("calibers") or []
            lines = []
            for c in caps:
                lines.append(f"- 关联业务板块：**{c['name']}**（{c['description']}）")
            for r in rules[:3]:
                lines.append(f"- 相关业务规则：**{r['title']}** —— {r['business_action']}")
            for cal in cals[:3]:
                lines.append(f"- 相关指标口径：**{cal['metric_name']}** —— {cal['formula_summary']}")
            if lines:
                sections.append("为您找到与当前提问相关的业务资产：\n" + "\n".join(lines))

    if backing_verdicts:
        backed = sum(1 for v in backing_verdicts if v["logic_backing_count"] > 0)
        unbacked = len(backing_verdicts) - backed
        sections.append(
            f"**【底层实证校验摘要】** 本次共核验 {len(backing_verdicts)} 项业务资产，"
            f"其中 {backed} 项已获得系统实际处理逻辑交叉实证"
            + (f"，{unbacked} 项尚无系统逻辑支撑。" if unbacked else "。")
        )

    if not sections:
        return (
            f"针对「{q}」，目前在已接入的制度与业务逻辑资产中没有找到直接对应的内容。"
            "您可以补充相关的监管制度文件或系统计算逻辑后再行查询。"
        )
    return "\n\n".join(sections)


def _collect_disclosures_and_citations(
    tool_outputs: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    disclosures: List[Dict[str, Any]] = []
    seen_disc = set()
    citations: List[Dict[str, Any]] = []
    seen_codes = set()
    # 白名单需覆盖工具结果中返回给模型的全部资产，避免模型引用第 13~15 条有效规则时被误判越界
    max_citations = 60

    def _append_citation(asset_kind: str, code: str, title: str, reconcile_status: str) -> None:
        if len(citations) >= max_citations or not code or code in seen_codes:
            return
        seen_codes.add(code)
        citations.append(
            {
                "asset_kind": asset_kind,
                "code": code,
                "title": title,
                "reconcile_status": reconcile_status,
            }
        )

    for out in tool_outputs:
        if out.get("disclose"):
            kind = out.get("disclose_kind")
            text = out.get("disclose_text")
            if kind not in seen_disc:
                seen_disc.add(kind)
                disclosures.append({"kind": kind, "text": text})
            elif text:
                for existing in disclosures:
                    if existing["kind"] == kind and len(text) > len(existing.get("text") or ""):
                        existing["text"] = text

        for cap in (out.get("covered_businesses") or []) + (out.get("unrealized_businesses") or []) + (out.get("capabilities") or []):
            _append_citation("CAPABILITY", str(cap.get("code") or ""), cap["name"], cap.get("coverage_status", "COVERED"))
        for rule in out.get("rules") or []:
            _append_citation("RULE", str(rule.get("code") or ""), rule["title"], rule.get("reconcile_status", "VERIFIED"))
        for cal in out.get("calibers") or []:
            _append_citation("CALIBER", str(cal.get("code") or ""), cal["metric_name"], cal.get("reconcile_status", "VERIFIED"))
    return disclosures, citations


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


def _normalize_planned_tool_args(tool_name: str, raw_args: Any, clean_query: str, role_lens: str) -> Dict[str, Any]:
    args = raw_args if isinstance(raw_args, dict) else {}
    fallback_text = sanitize_business_text(
        str(
            args.get("topic")
            or args.get("metric")
            or args.get("concept")
            or args.get("query")
            or args.get("keyword")
            or args.get("domain")
            or clean_query
        )
    ) or clean_query
    sub_domain = sanitize_business_text(str(args.get("sub_domain") or "")).strip()
    subject_theme = normalize_financial_theme(str(args.get("subject_theme") or ""))

    if tool_name == "list_covered_businesses":
        return {}
    if tool_name == "check_regulatory_coverage_gap":
        topic_val = sanitize_business_text(str(args.get("topic") or args.get("query") or "")).strip()
        return {"topic": topic_val} if topic_val else {}
    if tool_name == "get_business_scope_and_rules":
        out: Dict[str, Any] = {"topic": sanitize_business_text(str(args.get("topic") or fallback_text)) or clean_query}
        if sub_domain:
            out["sub_domain"] = sub_domain
        if subject_theme:
            out["subject_theme"] = subject_theme
        return out
    if tool_name == "get_business_caliber_detail":
        out = {"metric": sanitize_business_text(str(args.get("metric") or fallback_text)) or clean_query}
        if sub_domain:
            out["sub_domain"] = sub_domain
        if subject_theme:
            out["subject_theme"] = subject_theme
        return out
    if tool_name == "resolve_business_concept":
        return {"concept": sanitize_business_text(str(args.get("concept") or fallback_text)) or clean_query}
    if tool_name == "find_rules_by_business_role":
        return {"role": sanitize_business_text(str(args.get("role") or role_lens)) or role_lens}
    return {}


def _plan_tools_with_llm(
    session: Session,
    *,
    clean_query: str,
    role_lens: str,
    recent_history: List[Dict[str, str]],
    active_llm: Any,
    bus: Any,
) -> Tuple[List[Tuple[str, Dict[str, Any]]], bool]:
    capabilities = list_capabilities(session)
    domain_catalog = [
        {
            "name": c["name"],
            "regime": c.get("regime", "通用监管"),
            "sub_domains": [s["name"] for s in (c.get("sub_domains") or [])[:6]],
            "subject_themes": c.get("subject_themes") or [],
            "coverage_status": c["coverage_status"],
            "rule_count": c["rule_count"],
            "caliber_count": c["caliber_count"],
        }
        for c in capabilities[:20]
    ]
    tool_catalog = [
        dict(t)
        for t in BUSINESS_TOOL_SCHEMAS
        if t.get("name") != "verify_implementation_backing"
    ]
    try:
        planned_args, _trace = call_llm_validated(
            active_llm,
            "plan_copilot_tools",
            (
                "根据业务人员的当前问题、上下文对话与已收录业务域目录，自主规划 1 至 4 步最合适的纯业务查询工具组合。"
                "参数必须严格匹配参数名：get_business_scope_and_rules 用 topic（可选传 sub_domain 二级子专题、subject_theme 金融十大主题作跨报表横切检索）；"
                "get_business_caliber_detail 用 metric（可选传 subject_theme）；"
                "resolve_business_concept 用 concept；find_rules_by_business_role 用 role；list_covered_businesses 传 {}；"
                "check_regulatory_coverage_gap 仅当用户问全局未落地/全景盘点时传 {}，针对特定指标/主题一致性核对务必传 {\"topic\": \"主题词\"}。"
                "严禁出现任何物理表名、字段名、代码或 SQL 关键词。"
            ),
            {
                "question": clean_query,
                "role_lens": role_lens,
                "recent_history": recent_history,
                "available_businesses": domain_catalog,
                "available_subject_themes": list(FINANCIAL_SUBJECT_THEMES),
                "available_tools": tool_catalog,
            },
            backoff=(0.05, 0.1),
            bus=bus,
            validation_attempts=2,
        )
    except WorkflowError:
        return [], False

    raw_calls = planned_args.get("tool_calls") or []
    allowed_tools = {t["name"] for t in tool_catalog}
    plan: List[Tuple[str, Dict[str, Any]]] = []
    seen = set()
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        tname = str(item.get("tool") or "").strip()
        if tname not in allowed_tools:
            continue
        norm_args = _normalize_planned_tool_args(tname, item.get("args"), clean_query, role_lens)
        sig = (tname, json.dumps(norm_args, sort_keys=True, ensure_ascii=False))
        if sig in seen:
            continue
        seen.add(sig)
        plan.append((tname, norm_args))
        if len(plan) >= 4:
            break
    return plan, bool(plan)


def ask_business_copilot(
    session: Session,
    query: str,
    *,
    session_id: Optional[str] = None,
    role_lens: str = "业务",
    settings: Optional[Settings] = None,
    llm: Any = None,
) -> Dict[str, Any]:
    """执行一轮完整的纯业务 Copilot 问答，并持久化会话与可追溯依据。"""
    clean_query = (query or "").strip()
    if not clean_query:
        raise ValueError("提问内容不能为空")

    active_settings = get_effective_settings(session, settings or get_settings())
    sid = session_id or str(uuid.uuid4())
    bind_session_lock(session, _get_copilot_lock(sid), "_held_copilot_locks", sid)

    chat_session = session.get(CopilotSessionModel, sid)
    if chat_session is None:
        chat_session = CopilotSessionModel(
            id=sid,
            title=clean_query[:60],
            role_lens=role_lens,
        )
        session.add(chat_session)
        session.flush()
    else:
        chat_session.role_lens = role_lens
        chat_session.updated_at = datetime.utcnow()

    user_msg = CopilotMessageModel(
        id=str(uuid.uuid4()),
        session_id=sid,
        role="user",
        content=clean_query,
    )
    session.add(user_msg)
    session.flush()

    recent_rows = (
        session.query(CopilotMessageModel)
        .filter(CopilotMessageModel.session_id == sid, CopilotMessageModel.id != user_msg.id)
        .order_by(CopilotMessageModel.created_at.desc())
        .limit(6)
        .all()
    )
    recent_history = [
        {"role": m.role, "content": m.content[:400]}
        for m in reversed(recent_rows)
    ]

    budget = get_budget(session)
    configure_llm_concurrency(int(budget.get("max_llm_concurrency", 4)))
    bus = build_run_bus(budget)
    active_llm = llm
    if active_llm is None and active_settings.llm_provider != "scripted" and active_settings.llm_api_key:
        active_llm = RealDesignLlm(active_settings)

    follow_ups: List[str] = [
        "系统里有哪些客户或账户会被自动剔除不报？",
        "哪些制度要求是我们当前系统逻辑还没包含的？",
        "交易后余额和跨境联名账户目前分别按什么口径算？",
    ]
    tool_outputs: List[Dict[str, Any]] = []
    tool_trace: List[Dict[str, Any]] = []
    tool_planned_by_ai = False

    def _run_tool_step(tool_name: str, args: Dict[str, Any]) -> None:
        out = execute_business_tool(session, tool_name, args)
        tool_outputs.append(out)
        tool_trace.append(
            {
                "step": len(tool_trace) + 1,
                "tool": tool_name,
                "args": args,
                "disclose": bool(out.get("disclose")),
                "result_count": (
                    len(out.get("covered_businesses") or [])
                    or len(out.get("rules") or [])
                    or len(out.get("calibers") or [])
                    or len(out.get("unrealized_items") or [])
                ),
            }
        )

    with llm_execution_scope(bus=bus):
        if active_llm is not None:
            route_plan, tool_planned_by_ai = _plan_tools_with_llm(
                session,
                clean_query=clean_query,
                role_lens=role_lens,
                recent_history=recent_history,
                active_llm=active_llm,
                bus=bus,
            )
            if not route_plan:
                route_plan = _route_tools_for_query(clean_query, role_lens)
                tool_planned_by_ai = False
        else:
            route_plan = _route_tools_for_query(clean_query, role_lens)

        is_global_gap_query = any(
            k in clean_query
            for k in ("哪些业务", "全景", "盘点", "全部", "所有", "还没", "未落地", "未包含", "缺口", "差距")
        )
        executed_sigs = set()
        for tool_name, args in route_plan:
            effective_args = dict(args)
            if tool_name == "check_regulatory_coverage_gap" and not effective_args.get("topic") and not is_global_gap_query:
                effective_args["topic"] = clean_query
            sig = (tool_name, json.dumps(effective_args, sort_keys=True, ensure_ascii=False))
            executed_sigs.add(sig)
            _run_tool_step(tool_name, effective_args)

        disclosures, citations = _collect_disclosures_and_citations(tool_outputs)

        # 若 AI 规划的工具组合未命中任何资产且未覆盖确定性路由，补齐确定性查询以确保完整依据
        if tool_planned_by_ai and not citations:
            for tool_name, args in _route_tools_for_query(clean_query, role_lens):
                effective_args = dict(args)
                if tool_name == "check_regulatory_coverage_gap" and not effective_args.get("topic") and not is_global_gap_query:
                    effective_args["topic"] = clean_query
                sig = (tool_name, json.dumps(effective_args, sort_keys=True, ensure_ascii=False))
                if sig not in executed_sigs:
                    executed_sigs.add(sig)
                    _run_tool_step(tool_name, effective_args)
            disclosures, citations = _collect_disclosures_and_citations(tool_outputs)

        # 再自动调用一次底层实证核验工具，确保所有回答的规则/口径均有清晰支撑定论
        asset_codes = [c["code"] for c in citations if c["asset_kind"] in {"RULE", "CALIBER"}][:8]
        backing_verdicts: List[Dict[str, Any]] = []
        if asset_codes:
            backing_res = execute_business_tool(session, "verify_implementation_backing", {"asset_codes": asset_codes})
            backing_verdicts = backing_res.get("verdicts") or []
            tool_trace.append(
                {
                    "step": len(tool_trace) + 1,
                    "tool": "verify_implementation_backing",
                    "args": {"asset_count": len(asset_codes)},
                    "disclose": bool(backing_res.get("disclose")),
                    "result_count": len(backing_verdicts),
                }
            )
            if backing_res.get("disclose"):
                extra_disc = {"kind": backing_res.get("disclose_kind"), "text": backing_res.get("disclose_text")}
                if not any(d["kind"] == extra_disc["kind"] for d in disclosures):
                    disclosures.append(extra_disc)

        if active_llm is not None:
            allowed_codes = [c["code"] for c in citations]
            llm_args, _trace = call_llm_validated(
                active_llm,
                "compose_business_answer",
                (
                    "结合业务资产与实证校验结论作答，采用「先给 2-3 句明确结论，再分点用短句说明逻辑及原因」的精炼格式"
                    "（单主题问答建议控制在 400~800 字，全景盘点不超过 1200 字，禁止同义车轱辘重复）；"
                    "cited_asset_codes 只能填写 allowed_asset_codes 中实际存在的编号；"
                    "如系统存在多口径分歧或制度未落地需如实点明。严禁出现任何代码、SQL、物理表名或字段名。"
                ),
                {
                    "question": clean_query,
                    "role_lens": role_lens,
                    "recent_history": recent_history,
                    "tool_outputs": tool_outputs,
                    "backing_verdicts": backing_verdicts,
                    "disclosures": disclosures,
                    "allowed_asset_codes": allowed_codes,
                },
                backoff=(0.1, 0.2),
                bus=bus,
            )
            raw_answer = str(llm_args.get("answer") or "")
            follow_ups = llm_args.get("follow_up_questions") or follow_ups
            cited_set = set(str(x) for x in (llm_args.get("cited_asset_codes") or []) if x)
            if cited_set:
                citations = (
                    [c for c in citations if c["code"] in cited_set]
                    + [c for c in citations if c["code"] not in cited_set]
                )[:16]
            else:
                citations = citations[:16]
        else:
            raw_answer = _deterministic_business_narrative(clean_query, tool_outputs, backing_verdicts)
            citations = citations[:16]

    # 强制去技术化二次检查（防任何漏网代码词）
    jargon_hits = detect_technical_jargon(raw_answer)
    if jargon_hits:
        raw_answer = sanitize_business_text(raw_answer)

    # 强制前置披露文本（disclose=true 逐字置顶，绝不吞掉缺失状态）
    if disclosures:
        header = "\n".join(d["text"] for d in disclosures if d.get("text"))
        if header and header not in raw_answer:
            answer = f"{header}\n\n{raw_answer}"
        else:
            answer = raw_answer
    else:
        answer = raw_answer

    verification = {
        "de_jargon_passed": len(detect_technical_jargon(answer)) == 0,
        "residual_jargon": detect_technical_jargon(answer),
        "disclosures_count": len(disclosures),
        "cited_asset_count": len(citations),
        "backing_checked": len(backing_verdicts),
        "ai_powered": active_llm is not None,
        "tool_planned_by_ai": tool_planned_by_ai,
        "llm_model": _resolve_llm_model_name(active_llm, active_settings),
    }

    assistant_msg = CopilotMessageModel(
        id=str(uuid.uuid4()),
        session_id=sid,
        role="assistant",
        content=answer,
        disclosures_json=disclosures,
        cited_assets_json=citations,
        tool_trace_json=tool_trace,
        verification_json=verification,
    )
    session.add(assistant_msg)
    session.commit()

    return {
        "session_id": sid,
        "message_id": assistant_msg.id,
        "role_lens": role_lens,
        "answer": answer,
        "disclosures": disclosures,
        "cited_assets": citations,
        "tool_trace": tool_trace,
        "follow_up_questions": follow_ups,
        "verification": verification,
    }


def list_copilot_sessions(session: Session) -> List[Dict[str, Any]]:
    rows = session.query(CopilotSessionModel).order_by(CopilotSessionModel.updated_at.desc()).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "role_lens": r.role_lens,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


def get_copilot_session_detail(session: Session, session_id: str) -> Dict[str, Any]:
    chat = session.get(CopilotSessionModel, session_id)
    if chat is None:
        raise KeyError("Copilot session not found")
    msgs = (
        session.query(CopilotMessageModel)
        .filter(CopilotMessageModel.session_id == session_id)
        .order_by(CopilotMessageModel.created_at.asc())
        .all()
    )
    return {
        "id": chat.id,
        "title": chat.title,
        "role_lens": chat.role_lens,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "disclosures": m.disclosures_json or [],
                "cited_assets": m.cited_assets_json or [],
                "tool_trace": m.tool_trace_json or [],
                "verification": m.verification_json or {},
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ],
    }
