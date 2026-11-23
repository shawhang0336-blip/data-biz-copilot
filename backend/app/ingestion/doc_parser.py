"""监管与业务制度材料确定性解析器（接入层 Layer 3）。

支持 Markdown（含 YAML frontmatter 与网页公开法规汇编）/ TXT / Word (.docx) / Excel (.xlsx)，
按章节与业务关键词抽取结构化条款（能力总览、纳入条件、剔除例外、分支处理、指标口径、岗位关注）。
"""

import io
import re
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
from openpyxl import load_workbook

from app.infra.contracts import ExtractedClause
from app.infra.errors import WorkflowError
from app.infra.llm import call_llm_validated
from app.kernel.run import BudgetExceededError
from app.policy.dc_bus import sanitize_business_text

_NAV_NOISE_EXACT = {
    "术语表",
    "网站地图",
    "无障碍浏览",
    "无障碍浏览 English Version",
    "信息公开",
    "新闻发布",
    "法律法规",
    "货币政策",
    "宏观审慎",
    "信贷政策",
    "金融市场",
    "金融稳定",
    "调查统计",
    "银行会计",
    "支付体系",
    "金融科技",
    "人民币",
    "经理国库",
    "国际交往",
    "人员招录",
    "学术交流",
    "征信管理",
    "反洗钱",
    "党建工作",
    "服务互动",
    "政务公开",
    "政策解读",
    "公告信息",
    "图文直播",
    "央行研究",
    "音频视频",
    "市场动态",
    "网上展厅",
    "报告下载",
    "报刊年鉴",
    "网送文告",
    "办事大厅",
    "在线申报",
    "下载中心",
    "网上调查",
    "意见征集",
    "金融知识",
    "关于我们",
    "高级搜索",
    "字号 大 中 小",
    "打印本页　 关闭窗口",
    "打印本页 关闭窗口",
    "下载word版 下载pdf版",
    "中国人民银行规章",
    "中国人民银行发布",
}

_NAV_NOISE_PREFIXES = (
    "| 我的位置:",
    "发布日期:",
    "原始链接:",
    "http://",
    "https://",
)


def _split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """解析并剥离 Markdown 顶部的 YAML frontmatter（如 sql_explorer metrics/domains 卡）。"""
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}, text
    meta: Dict[str, str] = {}
    end_idx = -1
    for i in range(1, min(len(lines), 40)):
        if lines[i].strip() == "---":
            end_idx = i
            break
        if ":" in lines[i]:
            k, v = lines[i].split(":", 1)
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    if end_idx == -1:
        return {}, text
    body = "\n".join(lines[end_idx + 1 :]).strip()
    return meta, body


def _is_noise_line(line: str, filename: str) -> bool:
    if line in _NAV_NOISE_EXACT:
        return True
    if any(line.startswith(p) for p in _NAV_NOISE_PREFIXES):
        return True
    if line.startswith("<!--") and line.endswith("-->"):
        return True
    if "copilot_regression.py" in line or "COPILOT_TERMS_ENABLED" in line:
        return True
    if filename.endswith(".md") and line.startswith("变更记录：v0 样例"):
        return True
    # 清理纯模板前缀行，避免污染条款描述
    if line in {"口径细则（样例——行内请替换为业务确认后的正式文本）：", "圈定规则细则（样例——行内请替换为业务确认后的正式文本）：", "判定规则细则（样例——行内请替换为业务确认后的正式文本）："}:
        return True
    return False


def _infer_clause_kind(title: str, body: str, meta: Optional[Dict[str, Any]] = None) -> str:
    meta = meta or {}
    t = (title or "").strip()
    combined = f"{t} {body}"

    # 优先依据标题与元数据区分真指标口径 vs 制度规则，避免普通条文被误判成指标卡
    if any(k in t for k in ("剔除", "不纳入", "排除", "漏报", "应报未报", "未报")):
        return "EXCLUSION"
    if any(k in t for k in ("分支", "分类", "个人卡", "对公", "统括", "分别", "境内外对公客户", "向下继承")):
        return "BRANCH"
    if any(k in t for k in ("纳入", "圈定", "报送范围", "申报范围", "归并", "捞回", "主体范围")):
        return "INCLUSION"
    if any(k in t for k in ("岗位", "角色", "合规关注")):
        return "ROLE_GUIDE"

    # 长行政条文标题（非第五条大额标准）不当作单指标口径卡
    is_statute_article = bool(re.match(r"^第[一二三四六七八九十百0-9]+条", t))
    is_explicit_caliber_title = (
        any(k in t for k in ("公式", "口径", "算式", "折算余额", "交易金额", "交易后余额", "大额交易报送标准", "授信额度", "30 万美元"))
        and not any(k in t for k in ("办法", "废止", "答记者问", "通知", "实施方案", "圈定规则", "统计口径管理"))
        and not is_statute_article
    )
    is_meta_caliber = bool(meta.get("formula")) and "rule" not in meta.get("name", "").lower() and "scope" not in meta.get("name", "").lower()
    if is_explicit_caliber_title or is_meta_caliber:
        return "CALIBER"

    if any(k in t for k in ("专项核验", "专项监测", "数字人民币", "一表通")) or any(
        k in combined for k in ("超过九十天延期收付汇", "数字人民币大额交易")
    ):
        if "CALIBER" in t or "专项核验" in t:
            return "CALIBER"

    if any(k in combined for k in ("剔除", "不得纳入", "排除", "应报未报", "不报告")):
        return "EXCLUSION"
    if any(k in combined for k in ("分别", "分类", "个人卡", "对公", "统括", "境外客户优先")):
        return "BRANCH"
    if any(k in combined for k in ("应当报告", "纳入", "圈定", "报送范围", "申报范围", "采集", "提交大额交易报告")):
        return "INCLUSION"
    if any(k in combined for k in ("角色", "岗位", "职责", "合规")):
        return "ROLE_GUIDE"
    return "CAPABILITY"


def _infer_audience(text: str) -> str:
    if any(k in text for k in ("漏报", "风险", "补报", "监管核查", "合规", "反洗钱", "处罚")):
        return "业务 / 监管报送 / 合规"
    if any(k in text for k in ("余额", "金额", "折算", "联名", "授信", "资金来源", "资金使用")):
        return "业务 / 财务核算 / 监管报送"
    return "业务 / 监管报送"


def _infer_domain(chapter: str, title: str, body: str, meta: Optional[Dict[str, Any]] = None) -> str:
    meta = meta or {}
    head_text = f"{chapter} {title} {meta.get('name', '')} {meta.get('description', '')}"
    text = f"{head_text} {body}"

    if any(k in text for k in ("转口贸易", "延期收付汇", "离岸转口")):
        return "离岸转口贸易专项监测"
    if "数字人民币大额交易" in text or ("数字人民币" in title or "数字人民币" in chapter):
        return "数字人民币大额与可疑交易监管报送"
    if any(k in text for k in ("一表通", "互联网贷款", "信贷资产转让")):
        if "一表通" in head_text or "互联网贷款" in text or "信贷资产转让" in text or ("一表通" in body and len(body) < 800):
            return "一表通互联网贷款与信贷资产转让报送"
    if any(k in head_text for k in ("结售汇", "大额结汇", "大额购汇", "购汇资金来源", "结汇资金使用", "变动编号", "30万美元")) or any(
        k in text for k in ("机构大额结售汇", "购汇资金来源", "结汇资金使用")
    ):
        return "机构大额结售汇及资金来源与使用报送"
    if any(k in head_text for k in ("对公客户信息", "捞回", "境内外身份", "onshore_flg")):
        return "对公客户信息与境内外身份监管报送"
    if any(k in head_text for k in ("机构信息表", "银行机构信息", "层级架构")):
        return "银行机构信息与层级架构监管报送"
    if any(k in head_text for k in ("授信信息表", "综合授信", "授信额度")):
        return "客户综合授信额度信息监管报送"
    if any(k in head_text for k in ("应报未报", "漏报", "未匹配到报送位置", "unreported-account", "sac-unreported-account")):
        return "应报未报漏报风险识别"
    if any(k in head_text for k in ("分户账", "个人存款", "个体工商户", "迁移资产", "deposit-detail", "账户明细")):
        return "个人存款分户账明细加工"
    if any(k in body for k in ("应报未报风险账户", "缺少报送属地映射", "漏报风险识别")):
        return "应报未报漏报风险识别"
    if any(k in body for k in ("个人存款分户账", "个人存款交易流水")):
        return "个人存款分户账明细加工"
    return "大额与可疑外汇交易报送"


def _extract_metrics_mentioned(text: str) -> List[str]:
    candidates = (
        "交易金额",
        "交易后余额",
        "客户分户账余额",
        "跨境联名账户折算余额",
        "离岸转口贸易延期收付汇金额",
        "大额结售汇监测门槛",
        "综合授信最高额度",
    )
    return [item for item in candidates if item in text]


def _extract_raw_text(filename: str, content: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".docx"):
        doc = Document(io.BytesIO(content))
        parts: List[str] = []
        for para in doc.paragraphs:
            txt = (para.text or "").strip()
            if not txt:
                continue
            style_name = (getattr(para.style, "name", "") or "").lower()
            if "heading" in style_name or txt.startswith("第"):
                parts.append(f"## {txt}")
            else:
                parts.append(txt)
        for table in doc.tables:
            for row in table.rows[1:]:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if len(cells) >= 2:
                    parts.append(f"## {cells[0]}\n{'；'.join(cells[1:])}")
        return "\n".join(parts)
    if lower.endswith(".xlsx"):
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        lines: List[str] = []
        for sheet in wb.worksheets:
            lines.append(f"# {sheet.title}")
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            headers = [str(h or "").strip() for h in rows[0]]
            for row in rows[1:]:
                cells = [str(c or "").strip() for c in row]
                if not any(cells):
                    continue
                title = cells[0] or sheet.title
                details = "；".join(
                    f"{headers[i]}：{cells[i]}"
                    for i in range(1, min(len(headers), len(cells)))
                    if cells[i]
                )
                lines.append(f"## {title}\n{details or cells[0]}")
        return "\n".join(lines)
    return content.decode("utf-8", errors="replace")


def _clean_section_title(raw_title: str) -> str:
    t = re.sub(r"^[#*`>\s]+", "", raw_title).strip()
    t = re.sub(r"（样例[^）]*）", "", t).strip()
    t = t.rstrip("：: ")
    if len(t) > 56:
        t = t[:55].rstrip("，；： ") + "…"
    return t or "业务规范条款"


def _infer_doc_tier(filename: str) -> str:
    """区分宏观规划/政策宣贯类文件与可直接核对数据落地的制度规范。"""
    fn = filename or ""
    if any(k in fn for k in ("实施方案", "实施意见", "试点", "答记者问", "废止清单", "处罚通报", "会议文章")):
        return "MACRO_POLICY"
    return "ACTIONABLE_SPEC"


def _is_actionable_macro_clause(title: str, body: str) -> bool:
    """对宏观政策文件只保留与报送数据、合规口径、系统监测直接相关的条款，过滤纯党建/人才/绿色/基建条文。"""
    text = f"{title} {body}"
    data_keywords = (
        "一表通", "互联网贷款", "信贷资产转让", "数字人民币", "大额交易", "可疑交易",
        "监管报表", "监管数据", "EAST", "漏报", "错报", "数据质量", "统计", "报送",
    )
    non_data_noise = (
        "人才体系", "乡村振兴", "绿色金融", "绿色数据中心", "东数西算", "量子计算",
        "揭榜挂帅", "无障碍", "适老", "组织保障", "同业科技输出", "区域协同", "先进制造业",
    )
    if any(n in title for n in non_data_noise) and "一表通" not in text and "报送" not in title:
        return False
    return any(k in text for k in data_keywords)


_GENERIC_DOC_DOMAINS = {"通用业务规范", "通用监管合规", "宏观监管要求", "业务总则"}


def survey_document_sections(filename: str, doc_tier: str, clauses: List[ExtractedClause]) -> Dict[str, Any]:
    """构建监管与制度文档的轻量级全局结构总览（供 Plan 阶段 AI 审视整体目录与域归属）。"""
    canonical_domains = list(dict.fromkeys(c.business_domain for c in clauses))
    total_chars = sum(len(c.business_requirement or "") for c in clauses)
    return {
        "filename": filename,
        "doc_tier": doc_tier,
        "section_count": len(clauses),
        "total_chars": total_chars,
        "canonical_domains": canonical_domains,
        "sections": [
            {
                "section_index": idx,
                "chapter": c.chapter,
                "title": c.clause_title,
                "draft_domain": c.business_domain,
                "draft_kind": c.clause_kind,
                "char_count": len(c.business_requirement or ""),
                "preview": (c.business_requirement or "")[:180],
            }
            for idx, c in enumerate(clauses)
        ],
    }


def _default_doc_assignments(clauses: List[ExtractedClause]) -> List[Dict[str, Any]]:
    return [
        {
            "section_index": idx,
            "role": (
                "CALIBER_DEF"
                if c.clause_kind == "CALIBER"
                else ("CAPABILITY_SCOPE" if c.clause_kind == "CAPABILITY" else "RULE_CLAUSE")
            ),
            "business_domain": c.business_domain,
            "chunk_group": idx // 6,
            "reason": "",
        }
        for idx, c in enumerate(clauses)
    ]


def _resolve_consolidated_domain(
    proposed_domain: str,
    orig_domain: str,
    canonical_domains: List[str],
) -> str:
    clean = sanitize_business_text(proposed_domain).strip()
    if clean and (clean in canonical_domains or orig_domain in _GENERIC_DOC_DOMAINS):
        return clean
    return orig_domain


def _plan_document_structure_with_llm(
    filename: str,
    doc_tier: str,
    clauses: List[ExtractedClause],
    *,
    llm: Any,
    bus: Any,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Plan 阶段：先让 AI 审视整份文档结构与章节摘要，统一域归属、去噪标记与切块分组。"""
    default_assignments = _default_doc_assignments(clauses)
    if llm is None or len(clauses) < 2:
        return None, default_assignments

    survey = survey_document_sections(filename, doc_tier, clauses)
    canonical_domains = survey["canonical_domains"]
    try:
        plan_args, _trace = call_llm_validated(
            llm,
            "plan_document_structure",
            (
                "在逐章精细提炼条款前，先从全局视角审视整份监管/制度文档结构："
                "1. 判断 document_kind 并用 1-2 句纯中文概括 document_summary，确定 chunking_strategy；"
                "2. 为每个 section_index 分配 role（核心规则 RULE_CLAUSE / 口径指标 CALIBER_DEF / 业务范围 CAPABILITY_SCOPE / 无关前缀编者按目录套话 IGNORE）与 chunk_group（同主题连续编号）；"
                "3. business_domain 尽量复用 draft_domain 或 canonical_domains 中的标准域名称，避免同义拆分；"
                "4. 严禁输出任何 SQL、物理表名或字段名。"
            ),
            survey,
            backoff=(0.2, 0.5),
            bus=bus,
            validation_attempts=2,
        )
    except BudgetExceededError:
        raise
    except WorkflowError:
        return None, default_assignments

    valid_roles = {"RULE_CLAUSE", "CALIBER_DEF", "CAPABILITY_SCOPE", "IGNORE"}
    raw_assignments = plan_args.get("section_assignments") if isinstance(plan_args, dict) else []
    validated: List[Dict[str, Any]] = []
    seen_indices = set()
    for item in raw_assignments or []:
        if not isinstance(item, dict) or "section_index" not in item:
            continue
        try:
            idx = int(item["section_index"])
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(clauses) or idx in seen_indices:
            continue
        seen_indices.add(idx)
        role = str(item.get("role") or "").upper()
        if role not in valid_roles:
            role = default_assignments[idx]["role"]
        try:
            chunk_group = int(item.get("chunk_group", idx // 6))
        except (TypeError, ValueError):
            chunk_group = idx // 6
        orig = clauses[idx]
        resolved_domain = _resolve_consolidated_domain(
            str(item.get("business_domain") or ""),
            orig.business_domain,
            canonical_domains,
        )
        validated.append(
            {
                "section_index": idx,
                "role": role,
                "business_domain": resolved_domain,
                "chunk_group": chunk_group,
                "reason": sanitize_business_text(str(item.get("reason") or ""))[:120],
            }
        )

    for idx in range(len(clauses)):
        if idx not in seen_indices:
            validated.append(default_assignments[idx])

    validated.sort(key=lambda x: x["section_index"])
    if doc_tier == "ACTIONABLE_SPEC" and all(a["role"] == "IGNORE" for a in validated):
        for a in validated:
            a["role"] = default_assignments[a["section_index"]]["role"]

    consolidated_domains = list(dict.fromkeys(a["business_domain"] for a in validated if a["role"] != "IGNORE"))
    doc_plan = {
        "document_kind": str(plan_args.get("document_kind") or "REGULATORY_SPEC"),
        "document_summary": sanitize_business_text(str(plan_args.get("document_summary") or ""))[:240],
        "chunking_strategy": str(plan_args.get("chunking_strategy") or "HEADING_SECTIONS"),
        "canonical_domains": consolidated_domains or canonical_domains,
    }
    return doc_plan, validated


def _refine_clauses_with_llm(
    filename: str,
    doc_tier: str,
    clauses: List[ExtractedClause],
    *,
    llm: Any,
    bus: Any,
) -> List[ExtractedClause]:
    """Plan-then-Extract：先全局规划结构与去噪分块，再分批带全局上下文精炼条款并过零黑话门禁。"""
    if llm is None or not clauses:
        return clauses

    doc_plan, assignments = _plan_document_structure_with_llm(
        filename,
        doc_tier,
        clauses,
        llm=llm,
        bus=bus,
    )
    active_items = [a for a in assignments if a["role"] != "IGNORE"]
    if not active_items:
        return [] if doc_tier == "MACRO_POLICY" else clauses

    # 按 (business_domain, chunk_group) 归并成批，维持原文顺序
    grouped_order: List[Tuple[str, int]] = []
    grouped_map: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for a in active_items:
        key = (a["business_domain"], int(a["chunk_group"]))
        if key not in grouped_map:
            grouped_order.append(key)
            grouped_map[key] = []
        grouped_map[key].append(a)

    canonical_domains = (
        doc_plan["canonical_domains"]
        if doc_plan is not None
        else list(dict.fromkeys(c.business_domain for c in clauses))
    )
    refined: List[ExtractedClause] = []
    batch_size = 6

    for assigned_domain, chunk_group in grouped_order:
        group_entries = grouped_map[(assigned_domain, chunk_group)]
        for start in range(0, len(group_entries), batch_size):
            sub_batch = group_entries[start : start + batch_size]
            payload = {
                "filename": filename,
                "doc_tier": doc_tier,
                "canonical_domains": canonical_domains,
                "document_plan": {
                    **(doc_plan or {"canonical_domains": canonical_domains}),
                    "assigned_domain": assigned_domain,
                    "assigned_roles": sorted({a["role"] for a in sub_batch}),
                    "chunk_group": chunk_group,
                },
                "sections": [
                    {
                        "section_index": a["section_index"],
                        "chapter": clauses[a["section_index"]].chapter,
                        "title": clauses[a["section_index"]].clause_title,
                        "draft_domain": a["business_domain"],
                        "assigned_role": a["role"],
                        "draft_kind": clauses[a["section_index"]].clause_kind,
                        "content": clauses[a["section_index"]].business_requirement[:1200],
                    }
                    for a in sub_batch
                ],
            }
            try:
                args, _trace = call_llm_validated(
                    llm,
                    "extract_regulatory_clauses",
                    (
                        "结合 document_plan 提供的全文结构定位与指定业务域，将本批监管条款提炼为结构化纯中文业务条款："
                        "判断哪些属于真实可执行或可对照的业务/监管要求(keep=true)，剔除空泛套话(keep=false)，"
                        "并把任何英文表名/字段名改写为纯业务中文名称。business_domain 必须复用 draft_domain 或 canonical_domains 之一。"
                    ),
                    payload,
                    backoff=(0.2, 0.5),
                    bus=bus,
                )
            except BudgetExceededError:
                raise
            except WorkflowError:
                for a in sub_batch:
                    orig = clauses[a["section_index"]]
                    refined.append(
                        ExtractedClause(
                            chapter=orig.chapter,
                            business_domain=a["business_domain"],
                            clause_title=orig.clause_title,
                            clause_kind=orig.clause_kind,
                            business_requirement=orig.business_requirement,
                            audience=orig.audience,
                            metrics_mentioned=orig.metrics_mentioned,
                            source_locator={
                                **orig.source_locator,
                                "plan_stage": "PLAN_THEN_EXTRACT",
                                "plan_applied": doc_plan is not None,
                                "doc_plan_summary": (doc_plan or {}).get("document_summary", "")[:160],
                                "assigned_role": a["role"],
                                "chunk_group": chunk_group,
                            },
                        )
                    )
                continue

            items = args.get("clauses") if isinstance(args, dict) else None
            if not isinstance(items, list) or not items:
                for a in sub_batch:
                    refined.append(clauses[a["section_index"]])
                continue

            by_idx: Dict[int, Dict[str, Any]] = {}
            for x in items:
                if isinstance(x, dict) and "section_index" in x:
                    try:
                        by_idx[int(x["section_index"])] = x
                    except (TypeError, ValueError):
                        pass

            # 兼容 LLM 返回全局 section_index 或批内局部序号 0..k-1
            batch_indices = {a["section_index"] for a in sub_batch}
            matched_global = bool(set(by_idx.keys()) & batch_indices)
            kept_in_batch = False

            for pos, a in enumerate(sub_batch):
                orig_idx = a["section_index"]
                orig = clauses[orig_idx]
                ai_item = by_idx.get(orig_idx) if matched_global else by_idx.get(pos)
                locator_extra = {
                    "plan_stage": "PLAN_THEN_EXTRACT",
                    "plan_applied": doc_plan is not None,
                    "doc_plan_summary": (doc_plan or {}).get("document_summary", "")[:160],
                    "assigned_role": a["role"],
                    "chunk_group": chunk_group,
                }
                if ai_item is None:
                    refined.append(
                        ExtractedClause(
                            chapter=orig.chapter,
                            business_domain=a["business_domain"],
                            clause_title=orig.clause_title,
                            clause_kind=orig.clause_kind,
                            business_requirement=orig.business_requirement,
                            audience=orig.audience,
                            metrics_mentioned=orig.metrics_mentioned,
                            source_locator={**orig.source_locator, **locator_extra},
                        )
                    )
                    kept_in_batch = True
                    continue
                if not ai_item.get("keep", True):
                    continue
                kept_in_batch = True
                resolved_domain = _resolve_consolidated_domain(
                    str(ai_item.get("business_domain") or ""),
                    a["business_domain"],
                    canonical_domains,
                )
                refined.append(
                    ExtractedClause(
                        chapter=orig.chapter,
                        business_domain=resolved_domain,
                        clause_title=str(ai_item.get("clause_title") or orig.clause_title).strip(),
                        clause_kind=str(ai_item.get("clause_kind") or orig.clause_kind),  # type: ignore[arg-type]
                        business_requirement=str(ai_item.get("business_requirement") or orig.business_requirement).strip(),
                        audience=str(ai_item.get("audience") or orig.audience).strip(),
                        metrics_mentioned=list(ai_item.get("metrics_mentioned") or orig.metrics_mentioned),
                        source_locator={**orig.source_locator, **locator_extra, "ai_refined": True},
                    )
                )
            if not kept_in_batch and doc_tier == "ACTIONABLE_SPEC":
                for a in sub_batch:
                    orig = clauses[a["section_index"]]
                    refined.append(
                        ExtractedClause(
                            chapter=orig.chapter,
                            business_domain=a["business_domain"],
                            clause_title=orig.clause_title,
                            clause_kind=orig.clause_kind,
                            business_requirement=orig.business_requirement,
                            audience=orig.audience,
                            metrics_mentioned=orig.metrics_mentioned,
                            source_locator={
                                **orig.source_locator,
                                "plan_stage": "PLAN_THEN_EXTRACT",
                                "plan_applied": doc_plan is not None,
                                "doc_plan_summary": (doc_plan or {}).get("document_summary", "")[:160],
                                "assigned_role": a["role"],
                                "chunk_group": chunk_group,
                            },
                        )
                    )
    return refined


def parse_regulatory_document(
    filename: str,
    content: bytes,
    *,
    llm: Any = None,
    bus: Any = None,
) -> List[ExtractedClause]:
    """将监管或业务制度文档确定性拆分为结构化业务条款，并在配置大模型时执行受控语义提炼。"""
    doc_tier = _infer_doc_tier(filename)
    raw_text = _extract_raw_text(filename, content)
    meta, text = _split_frontmatter(raw_text)

    default_chapter = meta.get("description") or meta.get("title") or "业务总则"
    default_chapter = default_chapter.split("——")[0].strip() if "——" in default_chapter else default_chapter
    current_chapter = _clean_section_title(default_chapter)
    sections: List[Dict[str, str]] = []
    current_title = _clean_section_title(meta.get("description") or meta.get("title") or "") if meta else ""
    buf: List[str] = []

    def _flush() -> None:
        nonlocal current_title, buf
        body = "\n".join(line for line in buf if line.strip()).strip()
        if current_title and body:
            sections.append({"chapter": current_chapter, "title": _clean_section_title(current_title), "body": body})
        buf = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _is_noise_line(line, filename):
            continue
        if line.startswith("# ") and not line.startswith("## "):
            _flush()
            current_chapter = _clean_section_title(line[2:].strip())
            current_title = _clean_section_title(meta.get("description") or "") if meta else ""
            continue
        heading_match = re.match(
            r"^(?:##+|第[一二三四五六七八九十百0-9]+[条章节][：:\s]*)\s*(.+)$",
            line,
        )
        if heading_match:
            _flush()
            prefix = ""
            if line.startswith("第"):
                art = re.match(r"^(第[一二三四五六七八九十百0-9]+[条章节])", line)
                if art:
                    prefix = art.group(1) + " "
            # 对“第X条 正文…”把条文首句作为小节标题，同时把完整正文放入 body
            content_part = heading_match.group(1).strip()
            if prefix:
                short_head = re.split(r"[。；]", content_part, maxsplit=1)[0][:36]
                current_title = f"{prefix}{short_head}".strip()
                buf.append(line)
            else:
                current_title = content_part
            continue
        cn_section = re.match(r"^[一二三四五六七八九十]+、\s*(.+)$", line)
        if cn_section and len(line) <= 45:
            _flush()
            current_title = cn_section.group(1).strip()
            continue
        # 政府/监管实施意见中如“（六）高效推动监管数字化智能化转型”或“31.深入推进监管大数据建设”需细粒切分，避免跨百行混成一个条款
        sub_section = re.match(r"^（[一二三四五六七八九十]+）\s*(.{2,40})$", line)
        if sub_section:
            _flush()
            current_title = sub_section.group(1).strip()
            continue
        item_section = re.match(r"^(\d+\.[^。\n]{2,40}[。:]?)", line)
        if item_section and len(line) > 40:
            _flush()
            current_title = item_section.group(1).rstrip("。: ")
            buf.append(line)
            continue
        if not current_title:
            current_title = current_chapter
        buf.append(line.lstrip("-• "))
    _flush()

    clauses: List[ExtractedClause] = []
    for idx, sec in enumerate(sections):
        if doc_tier == "MACRO_POLICY" and not _is_actionable_macro_clause(sec["title"], sec["body"]):
            continue
        kind = _infer_clause_kind(sec["title"], sec["body"], meta=meta)
        domain = _infer_domain(sec["chapter"], sec["title"], sec["body"], meta=meta)
        audience = meta.get("audience") or _infer_audience(f"{sec['title']} {sec['body']}")
        if "数据开发" in audience:
            audience = "业务 / 监管报送"
        clauses.append(
            ExtractedClause(
                chapter=sec["chapter"],
                business_domain=domain,
                clause_title=sec["title"],
                clause_kind=kind,
                business_requirement=sec["body"],
                audience=audience,
                metrics_mentioned=_extract_metrics_mentioned(f"{sec['title']} {sec['body']}"),
                source_locator={
                    "filename": filename,
                    "section_index": idx,
                    "chapter": sec["chapter"],
                    "doc_tier": doc_tier,
                },
            )
        )
    if llm is not None and clauses:
        return _refine_clauses_with_llm(filename, doc_tier, clauses, llm=llm, bus=bus)
    return clauses
