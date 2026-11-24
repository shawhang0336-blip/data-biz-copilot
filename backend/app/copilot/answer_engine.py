"""纯业务智能问答编排引擎（Copilot 层 Layer 5）。

职责：
1. 接收业务人员的自然语言提问，确定性规划并调用 7 个纯业务工具收集实证与资产；
2. 触发 `disclose=True` 时把风险与未落地说明原样前置披露，严禁掩盖；
3. 经过 `default_bus()`（零代码黑话门禁 + 实证引用白名单门禁）校验并留存会话账本。
"""

from datetime import datetime
import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
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

_TOOL_PROGRESS_LABELS: Dict[str, str] = {
    "list_covered_businesses": "盘点系统已涵盖业务板块",
    "resolve_business_concept": "映射业务概念与金融主题",
    "get_business_scope_and_rules": "检索纳入剔除与分支规则",
    "get_business_caliber_detail": "查询业务指标计算口径",
    "verify_implementation_backing": "交叉核验业务依据",
    "check_regulatory_coverage_gap": "排查制度覆盖与口径差异",
    "find_rules_by_business_role": "按角色视角筛选重点规则",
}


def _summarize_tool_args(args: Dict[str, Any]) -> str:
    if not isinstance(args, dict):
        return ""
    parts: List[str] = []
    for key in ("subject_theme", "sub_domain", "topic", "metric", "concept", "role"):
        val = str(args.get(key) or "").strip()
        if val:
            parts.append(val[:28] + ("…" if len(val) > 28 else ""))
            if len(parts) >= 2:
                break
    return " · ".join(parts)


_FOLLOWUP_MARKER = "---FOLLOWUPS---"


def _sanitize_preserving_whitespace(chunk: str) -> str:
    if not chunk or not detect_technical_jargon(chunk):
        return chunk
    lead_len = len(chunk) - len(chunk.lstrip())
    trail_len = len(chunk) - len(chunk.rstrip())
    lead = chunk[:lead_len]
    trail = chunk[len(chunk) - trail_len:] if trail_len else ""
    inner = sanitize_business_text(chunk)
    return f"{lead}{inner}{trail}"


def _sanitize_multiline_business_text(text: str) -> str:
    out_lines: List[str] = []
    for line in str(text or "").splitlines():
        if not line.strip():
            out_lines.append("")
        elif line.lstrip().startswith("|"):
            cells = line.split("|")
            out_lines.append("|".join(sanitize_business_text(c) if c.strip() and not set(c.strip()) <= set("-: ") else c for c in cells))
        else:
            out_lines.append(sanitize_business_text(line))
    return "\n".join(out_lines)


def _stream_compose_with_llm(
    active_llm: Any,
    *,
    clean_query: str,
    role_lens: str,
    recent_history: List[Dict[str, str]],
    tool_outputs: List[Dict[str, Any]],
    backing_verdicts: List[Dict[str, Any]],
    disclosures: List[Dict[str, Any]],
    emit_delta: Callable[[str], None],
    on_first_token: Callable[[], None],
) -> Tuple[str, List[str]]:
    system_prompt = (
        "你是面向银行及金融机构业务人员的纯中文业务问答专家。"
        "你只能使用纯正中文业务语言回答，严禁出现任何代码、SQL 关键字、物理表名、字段英文名或脚本文件名。"
    )
    user_payload = {
        "question": clean_query,
        "role_lens": role_lens,
        "recent_history": recent_history,
        "tool_outputs": tool_outputs,
        "backing_verdicts": backing_verdicts,
        "disclosures": disclosures,
    }
    user_prompt = (
        "请结合以下业务资产与实证核验结论直接作答，像一位懂业务又懂系统落地的资深同事那样讲清楚。\n"
        "表达原则：\n"
        "1. 先给 2-3 句直白结论：直接回答核心问题，避免空洞公文套话（如动辄“建立长效机制/形成闭环/承接载体”）；\n"
        "2. 自主判断是否展开系统实际加工过程：当问题涉及具体规则怎么落地、为什么剔除/捞回某类记录、指标怎么算、分支或兜底怎么走时，结合 `backing_verdicts` 里的 `processing_steps`，用通俗大白话把系统真实的处理步骤讲出来（比如先从哪汇总数据 → 怎么筛选或剔除特殊情况 → 遇到不同情况怎么分支计算或兜底 → 最后怎么去重输出），让业务人员看得懂、有画面感；如果问题只是宏观盘点已涵盖业务或列举制度缺口，则简洁直给，不必硬塞冗余步骤；\n"
        "   讲加工过程时请严格按 `processing_steps` 里的 `flow_label` 与 `order` 先后顺序，用「第一步、第二步…」的通俗步骤说出来；\n"
        "   当需要对比并行分支、不同口径或多种情形的处理差异时，优先用 Markdown 表格（表头行 + `|---|---|` 分隔行，不超过 4 列，单元格尽量短）代替大段文字；\n"
        "   可以在最后补充一个简短的「举例说明」（如“比如某笔……”），但例子必须完全来源于已提供资料里的条件与分支，严禁编造规则、数字或主体；\n"
        "   用日常口语表达，避免“承接载体、既定维度、形成闭环、长效机制”之类的公文腔；\n"
        "3. 如存在多口径分歧或制度尚未包含，请直接点明哪一步不一致或还没实现；\n"
        "4. 如 disclosures 中已有提示，正文无需逐字重复该提示框；严禁出现任何代码、SQL 关键字、物理表名、字段英文名或脚本文件名；单主题建议 400~850 字，全景盘点不超过 1200 字；\n"
        f"5. 在回答全部结束后，另起一行输出 `{_FOLLOWUP_MARKER}`，随后紧跟 3 个简短的业务追问建议（每行 1 个）。\n\n"
        f"核查资料：{json.dumps(user_payload, ensure_ascii=False)}"
    )

    guard_len = len(_FOLLOWUP_MARKER) + 2
    buf = ""
    in_followups = False
    followup_buf = ""
    body_chunks: List[str] = []
    first_sent = False

    def _push_body(raw_piece: str) -> None:
        nonlocal first_sent
        if not raw_piece:
            return
        safe_piece = _sanitize_preserving_whitespace(raw_piece)
        if not safe_piece:
            return
        if not first_sent:
            first_sent = True
            on_first_token()
        body_chunks.append(safe_piece)
        emit_delta(safe_piece)

    def _on_piece(piece: str) -> None:
        nonlocal buf, in_followups, followup_buf
        if in_followups:
            followup_buf += piece
            return
        buf += piece
        marker_idx = buf.find(_FOLLOWUP_MARKER)
        if marker_idx != -1:
            _push_body(buf[:marker_idx].rstrip())
            in_followups = True
            followup_buf = buf[marker_idx + len(_FOLLOWUP_MARKER):]
            buf = ""
            return
        if len(buf) > guard_len:
            flush_part = buf[:-guard_len]
            buf = buf[-guard_len:]
            _push_body(flush_part)

    active_llm.stream_text(system_prompt, user_prompt, _on_piece)

    if not in_followups and buf:
        _push_body(buf.rstrip())

    parsed_followups: List[str] = []
    if followup_buf.strip():
        for raw_line in followup_buf.splitlines():
            clean_line = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", raw_line).strip()
            clean_line = sanitize_business_text(clean_line)
            if clean_line:
                parsed_followups.append(clean_line)
            if len(parsed_followups) >= 3:
                break

    body_text = "".join(body_chunks).strip()
    if not body_text:
        raise RuntimeError("Streaming compose returned empty answer")
    return body_text, parsed_followups


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

    has_detail_tool = any(
        out.get("tool") in {"get_business_scope_and_rules", "get_business_caliber_detail"}
        for out in tool_outputs
    )
    if has_detail_tool and backing_verdicts:
        flow_groups: Dict[str, List[Dict[str, Any]]] = {}
        seen_flow = set()
        for verdict in backing_verdicts:
            for st in verdict.get("processing_steps") or []:
                sig = (str(st.get("stage") or ""), str(st.get("flow_summary") or "")[:50])
                if sig in seen_flow:
                    continue
                seen_flow.add(sig)
                flow_groups.setdefault(str(st.get("flow_label") or "加工流程"), []).append(st)
        flow_lines: List[str] = []
        total = 0
        for label, steps in flow_groups.items():
            if total >= 5:
                break
            flow_lines.append(f"**{label}**")
            for idx, st in enumerate(sorted(steps, key=lambda x: int(x.get("order") or 0)), start=1):
                if total >= 5:
                    break
                parts = []
                if st.get("flow_summary"):
                    parts.append(str(st["flow_summary"]))
                if st.get("branches"):
                    parts.append("分情况处理：" + "；".join(st["branches"][:2]))
                if st.get("exclusions"):
                    parts.append("剔除：" + "；".join(st["exclusions"][:2]))
                if st.get("dedup"):
                    parts.append("去重：" + str(st["dedup"]))
                if parts:
                    flow_lines.append(f"{idx}. **{st.get('stage') or '加工环节'}**：" + " ".join(parts))
                    total += 1
        if flow_lines:
            sections.append("**【系统实际加工过程（通俗说明）】**\n" + "\n".join(flow_lines))

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

    has_query_match = any(
        out.get("tool")
        in {
            "get_business_scope_and_rules",
            "get_business_caliber_detail",
            "resolve_business_concept",
            "find_rules_by_business_role",
        }
        and bool((out.get("rules") or []) or (out.get("calibers") or []) or (out.get("capabilities") or []))
        for out in tool_outputs
    )
    if has_query_match:
        disclosures = [d for d in disclosures if d.get("kind") != "not_found"]
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
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """执行一轮完整的纯业务 Copilot 问答，并持久化会话与可追溯依据。"""
    started_at = time.monotonic()

    def _emit(event: Dict[str, Any]) -> None:
        if progress_callback is None:
            return
        payload = dict(event)
        payload["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
        try:
            progress_callback(payload)
        except Exception:
            pass

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
        step_no = len(tool_trace) + 1
        step_id = f"tool-{step_no}"
        label = _TOOL_PROGRESS_LABELS.get(tool_name, "检索业务资产")
        arg_hint = _summarize_tool_args(args)
        _emit(
            {
                "type": "step",
                "id": step_id,
                "status": "running",
                "tool": tool_name,
                "label": label,
                "detail": arg_hint,
            }
        )
        out = execute_business_tool(session, tool_name, args)
        result_count = (
            len(out.get("covered_businesses") or [])
            or len(out.get("rules") or [])
            or len(out.get("calibers") or [])
            or len(out.get("unrealized_items") or [])
        )
        tool_outputs.append(out)
        tool_trace.append(
            {
                "step": step_no,
                "tool": tool_name,
                "args": args,
                "disclose": bool(out.get("disclose")),
                "result_count": result_count,
            }
        )
        _emit(
            {
                "type": "step",
                "id": step_id,
                "status": "done",
                "tool": tool_name,
                "label": label,
                "detail": arg_hint,
                "result_count": result_count,
                "disclose": bool(out.get("disclose")),
            }
        )

    with llm_execution_scope(bus=bus):
        _emit(
            {
                "type": "step",
                "id": "plan",
                "status": "running",
                "label": "理解问题意图，规划核查路径",
            }
        )
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
        _emit(
            {
                "type": "step",
                "id": "plan",
                "status": "done",
                "label": "已规划业务核查路径",
                "detail": f"将执行 {len(route_plan)} 项定向查询",
            }
        )

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
            _emit(
                {
                    "type": "step",
                    "id": "verify",
                    "status": "running",
                    "tool": "verify_implementation_backing",
                    "label": "交叉核验业务依据",
                    "detail": f"核对 {len(asset_codes)} 项规则与口径",
                }
            )
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
            _emit(
                {
                    "type": "step",
                    "id": "verify",
                    "status": "done",
                    "tool": "verify_implementation_backing",
                    "label": "交叉核验业务依据",
                    "result_count": len(backing_verdicts),
                    "disclose": bool(backing_res.get("disclose")),
                }
            )
            if backing_res.get("disclose"):
                extra_disc = {"kind": backing_res.get("disclose_kind"), "text": backing_res.get("disclose_text")}
                if not any(d["kind"] == extra_disc["kind"] for d in disclosures):
                    disclosures.append(extra_disc)

        _emit(
            {
                "type": "meta",
                "disclosures": disclosures,
                "cited_assets": citations[:16],
            }
        )
        _emit(
            {
                "type": "step",
                "id": "compose",
                "status": "running",
                "label": "综合制度条款与业务规则，梳理结论",
            }
        )

        def _emit_chunked_answer(text: str) -> None:
            is_test = bool(os.environ.get("PYTEST_CURRENT_TEST"))
            step_size = 18
            for i in range(0, len(text), step_size):
                _emit({"type": "answer_delta", "delta": text[i:i + step_size]})
                if not is_test:
                    time.sleep(0.012)

        streamed_directly = False
        if progress_callback is not None and active_llm is not None and hasattr(active_llm, "stream_text"):
            try:
                raw_answer, streamed_followups = _stream_compose_with_llm(
                    active_llm,
                    clean_query=clean_query,
                    role_lens=role_lens,
                    recent_history=recent_history,
                    tool_outputs=tool_outputs,
                    backing_verdicts=backing_verdicts,
                    disclosures=disclosures,
                    emit_delta=lambda delta: _emit({"type": "answer_delta", "delta": delta}),
                    on_first_token=lambda: _emit(
                        {
                            "type": "step",
                            "id": "compose",
                            "status": "done",
                            "label": "已整理结论，正在输出解答",
                        }
                    ),
                )
                if streamed_followups:
                    follow_ups = streamed_followups
                citations = citations[:16]
                streamed_directly = True
            except Exception:
                streamed_directly = False

        if not streamed_directly:
            if active_llm is not None:
                allowed_codes = [c["code"] for c in citations]
                llm_args, _trace = call_llm_validated(
                    active_llm,
                    "compose_business_answer",
                    (
                        "结合业务资产与实证校验结论用通俗大白话作答：先给 2-3 句直白结论；"
                        "再根据问题性质自主判断是否结合 `backing_verdicts` 中的 `processing_steps` 说明系统实际的加工步骤"
                        "（问具体规则/口径/分支怎么跑时，按 processing_steps 的 flow_label 与 order 先后顺序讲清从哪来、怎么筛、怎么分情况算或兜底、怎么去重；"
                        "对比多种情形时优先用 Markdown 表格；可附一个仅来源于已有条件的简短「举例说明」，严禁编造；宏观盘点则简洁直给，避免空泛公文腔）；"
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
            _emit(
                {
                    "type": "step",
                    "id": "compose",
                    "status": "done",
                    "label": "已整理结论，正在输出解答" if progress_callback is not None else "结构化解答已生成",
                }
            )
            if progress_callback is not None and raw_answer:
                _emit_chunked_answer(raw_answer)

    # 强制去技术化二次检查（防任何漏网代码词）
    jargon_hits = detect_technical_jargon(raw_answer)
    if jargon_hits:
        raw_answer = _sanitize_multiline_business_text(raw_answer)

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
