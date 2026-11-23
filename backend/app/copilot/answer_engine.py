"""纯业务智能问答编排引擎（Copilot 层 Layer 5）。

职责：
1. 接收业务人员的自然语言提问，确定性规划并调用 7 个纯业务工具收集实证与资产；
2. 触发 `disclose=True` 时把风险与未落地说明原样前置披露，严禁掩盖；
3. 经过 `default_bus()`（零代码黑话门禁 + 实证引用白名单门禁）校验并留存会话账本。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy.orm import Session

from app.copilot.business_tools import execute_business_tool
from app.copilot.disclosure import build_disclosure
from app.infra.config import Settings, get_settings
from app.infra.db_tables import CopilotMessageModel, CopilotSessionModel
from app.infra.llm import RealDesignLlm, call_llm_validated
from app.kernel.event_log import _SessionRLock, bind_session_lock
from app.policy.dc_bus import default_bus, detect_technical_jargon, sanitize_business_text

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
                        f"  - 业务触发条件：{r['business_condition']}\n"
                        f"  - 系统处理结论：{r['business_action']}\n"
                        f"  - 业务合规依据：{r['business_rationale']}"
                    )
                sections.append("\n".join(lines))

        elif t == "get_business_caliber_detail":
            cals = out.get("calibers") or []
            if cals:
                lines = ["相关业务指标的计算与统计口径说明如下："]
                for c in cals[:4]:
                    extra = f"\n  - ⚠️ 内部多口径分歧提示：{c['conflict_notes']}" if c.get("conflict_notes") else ""
                    lines.append(
                        f"- **{c['metric_name']}**：{c['business_definition']}\n"
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
                lines.append("• **需要业务拍板统一的口径分歧**：")
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
            code = cap.get("code")
            if code and code not in seen_codes:
                seen_codes.add(code)
                citations.append(
                    {
                        "asset_kind": "CAPABILITY",
                        "code": code,
                        "title": cap["name"],
                        "reconcile_status": cap.get("coverage_status", "COVERED"),
                    }
                )
        for rule in out.get("rules") or []:
            code = rule.get("code")
            if code and code not in seen_codes:
                seen_codes.add(code)
                citations.append(
                    {
                        "asset_kind": "RULE",
                        "code": code,
                        "title": rule["title"],
                        "reconcile_status": rule.get("reconcile_status", "VERIFIED"),
                    }
                )
        for cal in out.get("calibers") or []:
            code = cal.get("code")
            if code and code not in seen_codes:
                seen_codes.add(code)
                citations.append(
                    {
                        "asset_kind": "CALIBER",
                        "code": code,
                        "title": cal["metric_name"],
                        "reconcile_status": cal.get("reconcile_status", "VERIFIED"),
                    }
                )
    return disclosures, citations


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

    active_settings = settings or get_settings()
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

    route_plan = _route_tools_for_query(clean_query, role_lens)
    tool_outputs: List[Dict[str, Any]] = []
    tool_trace: List[Dict[str, Any]] = []

    for idx, (tool_name, args) in enumerate(route_plan, start=1):
        out = execute_business_tool(session, tool_name, args)
        tool_outputs.append(out)
        tool_trace.append(
            {
                "step": idx,
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

    bus = default_bus()
    active_llm = llm
    if active_llm is None and active_settings.llm_provider != "scripted" and active_settings.llm_api_key:
        active_llm = RealDesignLlm(active_settings)

    follow_ups: List[str] = [
        "系统里有哪些客户或账户会被自动剔除不报？",
        "哪些制度要求是我们当前系统逻辑还没包含的？",
        "交易后余额和跨境联名账户目前分别按什么口径算？",
    ]

    if active_llm is not None:
        allowed_codes = [c["code"] for c in citations]
        llm_args, _trace = call_llm_validated(
            active_llm,
            "compose_business_answer",
            "结合业务资产与实证校验结论，用通俗纯中文业务语言回答业务人员问题。严禁出现任何代码、SQL、物理表名或字段名。",
            {
                "question": clean_query,
                "role_lens": role_lens,
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
    else:
        raw_answer = _deterministic_business_narrative(clean_query, tool_outputs, backing_verdicts)

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
