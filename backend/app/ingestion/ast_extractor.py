"""基于 sqlglot 的通用 SQL AST 骨架抽取与纯业务语义翻译器（接入层 Layer 3）。

职责：
1. `extract_sql_ast_skeleton`：使用 `sqlglot` 多方言解析 SQL AST，抽取写入表、来源表、JOIN、时间窗口、
   等值/范围过滤、否定剔除、CASE 分支、窗口去重与聚合口径特征，全部物理符号严格留在内部骨架；
2. `synthesize_generic_business_semantics`：当遇陌生非 SAC 领域代码时，不依赖硬编码字典，直接将 AST 骨架与
   开发注释提炼为纯粹的中文业务解释；
3. `translate_evidence_with_llm`：当配置真实大模型时，通过 `Bus` 零黑话门禁校验将 AST 骨架转译为自然业务语言。
"""

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp

from app.infra.errors import WorkflowError
from app.infra.llm import call_llm_validated
from app.kernel.run import BudgetExceededError
from app.policy.dc_bus import detect_technical_jargon, sanitize_business_text

logging.getLogger("sqlglot").setLevel(logging.ERROR)

_DIALECT_ORDER = ("oracle", "postgres", "mysql", "tsql", "spark", None)


def _strip_leading_comments(stmt: str) -> str:
    """移除注释并剥离 PL/SQL 存储过程/条件包装外壳，聚焦内部可执行 DML 语句。"""
    s = re.sub(r"/\*.*?\*/", " ", stmt, flags=re.DOTALL)
    s = re.sub(r"--[^\n]*", " ", s).strip()
    if re.match(
        r"^(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+)?PROCEDURE|BEGIN\b|IF\b|ELSE\b|ELSIF\b)",
        s,
        flags=re.IGNORECASE,
    ):
        inner = re.search(r"\b(?:INSERT|MERGE|UPDATE|DELETE|WITH)\b", s, flags=re.IGNORECASE)
        if inner:
            s = s[inner.start():]
    return s.strip()


def _parse_ast_best_effort(stmt: str) -> Optional[exp.Expression]:
    clean = _strip_leading_comments(stmt).rstrip("; ")
    if not clean:
        return None
    for dialect in _DIALECT_ORDER:
        try:
            tree = sqlglot.parse_one(clean, read=dialect)
            if tree is not None and not isinstance(tree, exp.Command):
                return tree
        except Exception:  # noqa: BLE001
            continue
    return None


def _table_name(node: exp.Table) -> str:
    parts = [p for p in (node.db, node.name) if p]
    return ".".join(parts).upper() if parts else (node.name or "").upper()


def extract_sql_ast_skeleton(stmt: str) -> Dict[str, Any]:
    """用 sqlglot 解析 SQL AST，提取结构化逻辑特征（物理表与字段严格留存供内部审计）。"""
    tree = _parse_ast_best_effort(stmt)
    if tree is None:
        return {
            "ast_parsed": False,
            "statement_kind": "UNKNOWN",
            "target_tables": [],
            "source_tables": [],
            "cte_names": [],
            "join_count": 0,
            "has_date_window": False,
            "has_equality_filter": False,
            "exclusion_kinds": [],
            "case_branch_count": 0,
            "has_window_dedup": False,
            "distinct_used": False,
            "aggregate_funcs": [],
            "group_by_count": 0,
            "has_arithmetic": False,
        }

    stmt_kind = type(tree).__name__.upper()
    cte_names = [cte.alias_or_name.upper() for cte in tree.find_all(exp.CTE) if cte.alias_or_name]
    cte_set = set(cte_names)

    target_tables: List[str] = []
    if isinstance(tree, (exp.Insert, exp.Merge, exp.Update, exp.Delete, exp.Create)):
        this_tbl = tree.find(exp.Table)
        if this_tbl is not None:
            tname = _table_name(this_tbl)
            if tname and tname not in cte_set:
                target_tables.append(tname)

    source_tables: List[str] = []
    for tbl in tree.find_all(exp.Table):
        tname = _table_name(tbl)
        if not tname or tname == "DUAL" or tname in cte_set or tname in target_tables:
            continue
        if tname not in source_tables:
            source_tables.append(tname)

    joins = list(tree.find_all(exp.Join))
    betweens = list(tree.find_all(exp.Between))
    gt_lt = list(tree.find_all(exp.GTE)) + list(tree.find_all(exp.LTE)) + list(tree.find_all(exp.GT)) + list(tree.find_all(exp.LT))
    eqs = list(tree.find_all(exp.EQ))

    exclusion_kinds: List[str] = []
    for node in tree.find_all(exp.Not):
        if isinstance(node.this, exp.Exists):
            if "NOT_EXISTS" not in exclusion_kinds:
                exclusion_kinds.append("NOT_EXISTS")
        elif isinstance(node.this, exp.In):
            if "NOT_IN" not in exclusion_kinds:
                exclusion_kinds.append("NOT_IN")
        elif isinstance(node.this, exp.Is):
            if "IS_NOT_NULL" not in exclusion_kinds:
                exclusion_kinds.append("IS_NOT_NULL")
    if list(tree.find_all(exp.NEQ)) and "NEQ" not in exclusion_kinds:
        exclusion_kinds.append("NEQ")
    for is_node in tree.find_all(exp.Is):
        if isinstance(is_node.expression, exp.Null) and "IS_NULL" not in exclusion_kinds:
            exclusion_kinds.append("IS_NULL")

    cases = list(tree.find_all(exp.Case))
    case_branch_count = sum(len(c.args.get("ifs") or []) for c in cases)

    has_window_dedup = False
    for win in tree.find_all(exp.Window):
        fn_sql = win.this.sql().upper() if win.this else ""
        if any(k in fn_sql for k in ("ROW_NUMBER", "RANK", "DENSE_RANK")):
            has_window_dedup = True

    distinct_used = any(bool(s.args.get("distinct")) for s in tree.find_all(exp.Select))

    agg_map = (
        (exp.Sum, "SUM"),
        (exp.Count, "COUNT"),
        (exp.Avg, "AVG"),
        (exp.Max, "MAX"),
        (exp.Min, "MIN"),
    )
    aggregate_funcs = [label for cls, label in agg_map if list(tree.find_all(cls))]
    group_by_count = sum(len(g.expressions or []) for g in tree.find_all(exp.Group))
    has_arithmetic = bool(list(tree.find_all(exp.Mul)) or list(tree.find_all(exp.Div)) or list(tree.find_all(exp.Add)) or list(tree.find_all(exp.Sub)))

    return {
        "ast_parsed": True,
        "statement_kind": stmt_kind,
        "target_tables": target_tables,
        "source_tables": source_tables,
        "cte_names": cte_names,
        "join_count": len(joins),
        "has_date_window": bool(betweens or gt_lt),
        "has_equality_filter": bool(eqs),
        "exclusion_kinds": exclusion_kinds,
        "case_branch_count": case_branch_count,
        "has_window_dedup": has_window_dedup,
        "distinct_used": distinct_used,
        "aggregate_funcs": aggregate_funcs,
        "group_by_count": group_by_count,
        "has_arithmetic": has_arithmetic,
    }


def _clean_comment_topic(comments: List[str], filename: str) -> str:
    """从中文注释提炼纯粹的业务主题名，严禁透出表名/文件名/英文代码。"""
    for raw in comments:
        c = re.sub(r"^(?:任务描述|程序描述|程序名称|业务说明|功能说明|报表名称|主题|指标说明)[：:\s]*", "", raw).strip()
        c = sanitize_business_text(c)
        c = re.sub(r"[（(][^）)]*[）)]", "", c).strip()
        if len(c) >= 4 and not detect_technical_jargon(c):
            return c[:28]
    stem = sanitize_business_text(re.sub(r"\.[A-Za-z0-9]+$", "", filename).replace("_", " "))
    if any("\u4e00" <= ch <= "\u9fff" for ch in stem) and not detect_technical_jargon(stem):
        return stem[:24]
    return "待AI归类监管逻辑域"


def synthesize_generic_business_semantics(
    skeleton: Dict[str, Any],
    comments: List[str],
    filename: str,
) -> Dict[str, Any]:
    """不依赖特定行业字典，直接基于 sqlglot AST 结构与注释生成纯中文业务解释。"""
    topic = _clean_comment_topic(comments, filename)
    inclusions: List[Dict[str, Any]] = []
    exclusions: List[Dict[str, Any]] = []
    branches: List[Dict[str, Any]] = []

    if skeleton.get("join_count", 0) > 0:
        inclusions.append(
            {
                "rule_code": "GEN_INC_MULTI_SOURCE_MATCH",
                "title": f"{topic}多源信息关联归并纳入",
                "condition": f"基于多方业务台账（共 {skeleton['join_count']} 处关联核对）完成主体身份与业务属性匹配后纳入处理范围",
                "rationale": "确保输出的每条业务结果均具备完整关联的主体、账户及上下文业务属性。",
            }
        )
    if skeleton.get("has_date_window"):
        inclusions.append(
            {
                "rule_code": "GEN_INC_WINDOW_RANGE",
                "title": f"{topic}统计区间与阈值边界筛选",
                "condition": "仅纳入交易或业务日期处于目标统计周期区间内、且满足对应数值边界条件的记录",
                "rationale": "保证统计口径严格限定在指定业务发生窗口与有效阈值范围内，避免跨期或无效记录混入。",
            }
        )
    if not inclusions and skeleton.get("has_equality_filter"):
        inclusions.append(
            {
                "rule_code": "GEN_INC_STATE_FILTER",
                "title": f"{topic}业务状态与类别准入筛选",
                "condition": "仅纳入业务状态、类别或标识符合既定准入条件的有效记录",
                "rationale": "只将符合当前业务范围与有效状态的数据纳入正式结果清单。",
            }
        )

    excl_kinds = set(skeleton.get("exclusion_kinds") or [])
    if "NOT_EXISTS" in excl_kinds or "NOT_IN" in excl_kinds:
        exclusions.append(
            {
                "rule_code": "GEN_EXC_EXCLUSION_LIST",
                "title": "按排除对照清单或查重校验自动剔除",
                "condition": "若当前业务记录存在于指定排除清单、风控黑名单或已完成处理的历史流水清单中，则自动予以剔除",
                "rationale": "防止重复上报或将明确处于豁免/异常拦截清单中的业务误纳入正常结果。",
            }
        )
    if "NEQ" in excl_kinds:
        exclusions.append(
            {
                "rule_code": "GEN_EXC_INVALID_STATE",
                "title": "排除作废、撤回或特定例外状态记录",
                "condition": "过滤处于已作废、已冲正撤回或明确不予计入的特定例外状态记录",
                "rationale": "保障最终业务口径仅反映真实有效且未撤销的业务事实。",
            }
        )
    if "IS_NULL" in excl_kinds and not exclusions:
        exclusions.append(
            {
                "rule_code": "GEN_EXC_MISSING_ATTR",
                "title": "空缺关键属性专项识别与隔离过滤",
                "condition": "识别特定业务要素缺失的记录并按要求单独筛选或隔离处理",
                "rationale": "将信息残缺记录与正常完备记录分流管控。",
            }
        )

    if skeleton.get("case_branch_count", 0) > 0:
        branches.append(
            {
                "title": f"{topic}多情形分类判定分支",
                "condition": f"逻辑内部依据业务属性、状态或金额区间划分为 {skeleton['case_branch_count']} 类条件情形",
                "action": "针对不同业务分类分别执行差异化赋权、归口归类或折算逻辑",
            }
        )

    agg_funcs = skeleton.get("aggregate_funcs") or []
    if agg_funcs:
        calc_kind = "AGGREGATE"
        calc_summary = (
            f"按业务分类维度进行分组汇总统计（执行{'、'.join(agg_funcs)}指标聚合），"
            + ("并包含复合比例/加减折算。" if skeleton.get("has_arithmetic") else "用于输出归并后的汇总口径数值。")
        )
    elif skeleton.get("has_arithmetic"):
        calc_kind = "CONDITIONAL"
        calc_summary = "按既定业务公式对原始金额或指标执行折算、分摊或复合四则运算后输出。"
    elif skeleton.get("case_branch_count", 0) > 0:
        calc_kind = "CONDITIONAL"
        calc_summary = "依据不同业务情形走条件分支赋权后直出结果，未做跨笔加总。"
    else:
        calc_kind = "DIRECT"
        calc_summary = "按业务原始发生值直接核验传递，不进行汇总或复杂口径改写。"

    dedup_hint = ""
    if skeleton.get("statement_kind") == "MERGE":
        dedup_hint = "按核心业务唯一标识执行增量合并更新，确保重复执行时幂等覆盖、不产生重复台账。"
    elif skeleton.get("has_window_dedup"):
        dedup_hint = "按客户/账户与业务流水维度排序后仅保留唯一 1 条最新或最优先记录，严防重复计数。"
    elif skeleton.get("distinct_used"):
        dedup_hint = "按业务输出维度执行整行去重，确保同一记录不重复输出。"

    comment_summary = "；".join(
        sanitize_business_text(re.sub(r"^(?:任务描述|程序描述|程序名称|业务说明)[：:\s]*", "", c))
        for c in comments[:2]
        if c.strip()
    )
    source_summary = comment_summary or (
        f"负责「{topic}」的业务加工处理：整合相关业务信息，完成纳入筛选、例外剔除并形成最终业务结果。"
    )
    if skeleton.get("cte_names"):
        source_summary += f"（内部按 {len(skeleton['cte_names'])} 步分阶段递进加工汇总）"

    return {
        "business_domain_hint": topic,
        "target_concept_hint": topic,
        "source_summary": sanitize_business_text(source_summary),
        "inclusions": inclusions,
        "exclusions": exclusions,
        "branches": branches,
        "calculation_kind": calc_kind,
        "calculation_summary": calc_summary,
        "dedup_grain_hint": dedup_hint,
    }


def translate_evidence_with_llm(
    skeleton: Dict[str, Any],
    comments: List[str],
    fallback_evidence: Dict[str, Any],
    *,
    llm: Any,
    bus: Any,
) -> Dict[str, Any]:
    """若配置了真实 LLM，把 AST 结构特征与脱敏后的注释交给模型翻译为自然业务表达并经 Bus 门禁拦截。"""
    if llm is None:
        return fallback_evidence
    payload = {
        "ast_features": {
            "statement_kind": skeleton.get("statement_kind"),
            "join_count": skeleton.get("join_count"),
            "has_date_window": skeleton.get("has_date_window"),
            "has_equality_filter": skeleton.get("has_equality_filter"),
            "exclusion_kinds": skeleton.get("exclusion_kinds"),
            "case_branch_count": skeleton.get("case_branch_count"),
            "has_window_dedup": skeleton.get("has_window_dedup"),
            "distinct_used": skeleton.get("distinct_used"),
            "aggregate_funcs": skeleton.get("aggregate_funcs"),
            "has_arithmetic": skeleton.get("has_arithmetic"),
            "step_count": len(skeleton.get("cte_names") or []),
        },
        "developer_comments": [c for c in comments if c][:6],
        "deterministic_draft": fallback_evidence,
    }
    args, _trace = call_llm_validated(
        llm,
        "translate_logic_evidence",
        "将系统逻辑结构特征和注释翻译成通俗易懂、完全面向业务人员的中文业务解释（严禁出现任何表名、字段名、SQL 关键字或存储过程名称）。",
        payload,
        backoff=(0.1, 0.2),
        bus=bus,
    )
    merged = {**fallback_evidence}
    for key in (
        "business_domain_hint",
        "target_concept_hint",
        "source_summary",
        "calculation_summary",
        "dedup_grain_hint",
    ):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            merged[key] = sanitize_business_text(val)
    for list_key in ("inclusions", "exclusions", "branches"):
        val = args.get(list_key)
        if isinstance(val, list) and val:
            cleaned_list: List[Dict[str, Any]] = []
            for idx, item in enumerate(val):
                if not isinstance(item, dict):
                    continue
                cleaned_item = {
                    k: sanitize_business_text(str(v)) if isinstance(v, str) else v
                    for k, v in item.items()
                }
                if list_key in {"inclusions", "exclusions"} and not cleaned_item.get("rule_code"):
                    cleaned_item["rule_code"] = (
                        f"LLM_{list_key[:3].upper()}_{hashlib.md5(str(cleaned_item).encode('utf-8')).hexdigest()[:6]}"
                    )
                cleaned_list.append(cleaned_item)
            if cleaned_list:
                merged[list_key] = cleaned_list
    return merged


def _compute_upstream_step_map(evidences: List[Any]) -> Dict[int, List[int]]:
    """纯索引层步骤血缘：仅依据读写交叉计算步骤编号间的上下游引用，严禁输出任何物理表名。"""
    upstream_map: Dict[int, List[int]] = {}
    targets_by_idx: List[set] = [
        set((ev.internal_locator or {}).get("target_tables") or [])
        for ev in evidences
    ]
    for idx, ev in enumerate(evidences):
        src_tables = set((ev.internal_locator or {}).get("source_tables") or [])
        deps = [
            prev_idx
            for prev_idx in range(idx)
            if src_tables and (src_tables & targets_by_idx[prev_idx])
        ]
        upstream_map[idx] = deps
    return upstream_map


_GENERIC_CODE_DOMAINS = {
    "",
    "核心业务规则",
    "核心业务规则与逻辑加工",
    "通用业务规范",
    "通用业务数据处理域",
    "待AI归类监管逻辑域",
}


def survey_code_script(
    filename: str,
    banner: str,
    evidences: List[Any],
    comments_pool: List[str],
) -> Dict[str, Any]:
    """生成底层多步代码的纯业务全局骨架总览（含纯编号步间依赖 DAG，无物理表/字段名）。"""
    upstream_map = _compute_upstream_step_map(evidences)
    clean_comments = [
        sanitize_business_text(c)
        for c in dict.fromkeys(comments_pool)
        if c and not detect_technical_jargon(sanitize_business_text(c))
    ][:10]
    canonical_hints = list(
        dict.fromkeys(
            ev.business_domain_hint
            for ev in evidences
            if ev.business_domain_hint and ev.business_domain_hint not in _GENERIC_CODE_DOMAINS
        )
    )
    return {
        "step_count": len(evidences),
        "regulatory_regime": (evidences[0].internal_locator or {}).get("regime", "通用监管") if evidences else "通用监管",
        "procedure_banner": sanitize_business_text(banner),
        "canonical_domain_hints": canonical_hints,
        "business_comments": clean_comments,
        "steps": [
            {
                "step_index": idx,
                "statement_kind": str((ev.internal_locator or {}).get("statement_kind") or "SELECT"),
                "join_count": int((ev.internal_locator or {}).get("join_count") or 0),
                "upstream_steps": upstream_map[idx],
                "draft_domain": ev.business_domain_hint,
                "draft_concept": ev.target_concept_hint,
                "calculation_kind": ev.calculation_kind,
                "draft_summary": sanitize_business_text(ev.source_summary)[:160],
            }
            for idx, ev in enumerate(evidences)
        ],
    }


def _default_code_groups(evidences: List[Any]) -> List[Dict[str, Any]]:
    domain_order: List[str] = []
    by_domain: Dict[str, List[int]] = {}
    fallback_dom = next(
        (ev.business_domain_hint for ev in evidences if ev.business_domain_hint not in _GENERIC_CODE_DOMAINS),
        "核心业务规则与逻辑加工",
    )
    for idx, ev in enumerate(evidences):
        d = ev.business_domain_hint if ev.business_domain_hint not in _GENERIC_CODE_DOMAINS else fallback_dom
        if d not in by_domain:
            domain_order.append(d)
            by_domain[d] = []
        by_domain[d].append(idx)
    groups: List[Dict[str, Any]] = []
    for gid, domain in enumerate(domain_order):
        idxs = by_domain[domain]
        has_calc = any(evidences[i].calculation_kind in {"AGGREGATE", "CONFLICT"} for i in idxs)
        groups.append(
            {
                "group_id": gid,
                "stage_title": f"{domain}逻辑加工阶段",
                "business_domain": domain,
                "stage_role": "CALCULATION" if has_calc else "SCOPE_FILTER",
                "step_indices": idxs,
                "stage_purpose": f"围绕「{domain}」执行条件圈定、口径加工与核验输出。",
            }
        )
    return groups


def _plan_code_structure_with_llm(
    filename: str,
    banner: str,
    evidences: List[Any],
    comments_pool: List[str],
    *,
    is_sac_script: bool,
    llm: Any,
    bus: Any,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Plan 阶段：先让 AI 审视整份代码步骤 DAG 与业务注释，总结业务主线并将步骤划分为 1-4 个逻辑阶段。"""
    default_groups = _default_code_groups(evidences)
    if llm is None or len(evidences) < 2:
        return None, default_groups

    survey = survey_code_script(filename, banner, evidences, comments_pool)
    canonical_hints = survey["canonical_domain_hints"]
    try:
        args, _trace = call_llm_validated(
            llm,
            "plan_code_structure",
            (
                "在逐阶段精细翻译逻辑前，先根据 step_count、procedure_banner、steps（含步骤编号间上下游 upstream_steps 依赖）与 business_comments 审视整套逻辑全局："
                "1. 用纯中文概述整套处理流程的 business_overview 及主归属业务域 canonical_domain；"
                "2. 将 0 到 step_count-1 所有步骤不重不漏地归并划分为 1 至 4 个连续且职能清晰的逻辑阶段 chunk_groups"
                "（stage_role 取 SCOPE_FILTER/BRANCH_LOGIC/CALCULATION/DEDUP_MERGE/OUTPUT_REPORT；仅纯技术准备工作标 IGNORE）；"
                "3. 若同一份代码内部存在多个不同的 canonical_domain_hints（如同时包含主报送域与「应报未报漏报风险识别」），不同 draft_domain 的步骤应分别划分为独立阶段，保留各自原本的 draft_domain；"
                "若所有步骤属同一业务主题且 canonical_domain_hints 非空，应直接复用 canonical_domain_hints 中的标准域名，不要另造近义新词，且严禁原样回填占位词「待AI归类监管逻辑域」或出现表名/字段名/SQL。"
            ),
            survey,
            backoff=(0.2, 0.5),
            bus=bus,
            validation_attempts=2,
        )
    except BudgetExceededError:
        raise
    except WorkflowError:
        return None, default_groups

    valid_roles = {"SCOPE_FILTER", "BRANCH_LOGIC", "CALCULATION", "DEDUP_MERGE", "OUTPUT_REPORT", "IGNORE"}
    raw_groups = args.get("chunk_groups") if isinstance(args, dict) else []
    total_steps = len(evidences)
    used_steps = set()
    validated_groups: List[Dict[str, Any]] = []

    raw_canonical = sanitize_business_text(str(args.get("canonical_domain") or "")).strip()
    if raw_canonical in _GENERIC_CODE_DOMAINS:
        raw_canonical = ""
    if canonical_hints and (is_sac_script or not raw_canonical or any(h in raw_canonical or raw_canonical in h for h in canonical_hints)):
        canonical_domain = raw_canonical if raw_canonical in canonical_hints else canonical_hints[0]
    else:
        canonical_domain = raw_canonical or (canonical_hints[0] if canonical_hints else "核心业务规则与逻辑加工")

    for g in raw_groups or []:
        if not isinstance(g, dict):
            continue
        cleaned_indices: List[int] = []
        for raw_idx in g.get("step_indices") or []:
            try:
                idx = int(raw_idx)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < total_steps and idx not in used_steps:
                used_steps.add(idx)
                cleaned_indices.append(idx)
        if not cleaned_indices:
            continue

        role = str(g.get("stage_role") or "").upper()
        if role not in valid_roles:
            role = "SCOPE_FILTER"

        group_hints = [
            evidences[i].business_domain_hint
            for i in cleaned_indices
            if evidences[i].business_domain_hint and evidences[i].business_domain_hint not in _GENERIC_CODE_DOMAINS
        ]
        dominant_hint = group_hints[0] if group_hints else canonical_domain
        proposed_domain = sanitize_business_text(str(g.get("business_domain") or "")).strip()
        if proposed_domain in _GENERIC_CODE_DOMAINS:
            proposed_domain = ""
        if is_sac_script and canonical_hints:
            resolved_domain = proposed_domain if proposed_domain in canonical_hints else dominant_hint
        else:
            resolved_domain = (
                dominant_hint
                if (not proposed_domain or (dominant_hint not in _GENERIC_CODE_DOMAINS and (dominant_hint in proposed_domain or proposed_domain in dominant_hint)))
                else proposed_domain
            )

        validated_groups.append(
            {
                "group_id": len(validated_groups),
                "stage_title": sanitize_business_text(str(g.get("stage_title") or f"{resolved_domain}逻辑阶段")).strip()
                or f"{resolved_domain}逻辑阶段",
                "business_domain": resolved_domain,
                "stage_role": role,
                "step_indices": cleaned_indices,
                "stage_purpose": sanitize_business_text(
                    str(g.get("stage_purpose") or f"围绕「{resolved_domain}」执行业务筛选与口径处理。")
                ).strip(),
            }
        )

    # 兜底补齐未被模型覆盖的步骤，确保 100% 确定性全覆盖（对齐 dq_agent _validate_plan）
    missing = [i for i in range(total_steps) if i not in used_steps]
    if missing:
        missing_evs = [evidences[i] for i in missing]
        for extra in _default_code_groups(missing_evs):
            remapped = [missing[k] for k in extra["step_indices"]]
            extra["group_id"] = len(validated_groups)
            extra["step_indices"] = remapped
            if extra["business_domain"] in _GENERIC_CODE_DOMAINS:
                extra["business_domain"] = canonical_domain
            validated_groups.append(extra)

    if not validated_groups:
        return None, default_groups

    if all(g["stage_role"] == "IGNORE" for g in validated_groups):
        for g in validated_groups:
            g["stage_role"] = "SCOPE_FILTER"

    step_to_stage: Dict[int, str] = {}
    for g in validated_groups:
        for idx in g["step_indices"]:
            step_to_stage[idx] = g["stage_title"]

    code_plan = {
        "business_overview": sanitize_business_text(str(args.get("business_overview") or "")).strip()[:240]
        or sanitize_business_text(banner),
        "canonical_domain": canonical_domain,
        "step_to_stage": step_to_stage,
    }
    return code_plan, validated_groups


def _merge_rule_list(existing: List[Dict[str, Any]], incoming: Any, prefix: str) -> List[Dict[str, Any]]:
    if not isinstance(incoming, list) or not incoming:
        return existing
    by_code = {x.get("rule_code"): dict(x) for x in existing if x.get("rule_code")}
    for item in incoming:
        if not isinstance(item, dict):
            continue
        clean_item = {
            k: sanitize_business_text(str(v)) if (isinstance(v, str) and k != "rule_code") else v
            for k, v in item.items()
        }
        raw_rc = str(clean_item.get("rule_code") or "").strip()
        if raw_rc in by_code:
            by_code[raw_rc].update({k: v for k, v in clean_item.items() if k != "rule_code" and v})
        elif clean_item.get("title") and clean_item.get("condition"):
            # 新增AI提炼规则统一生成短哈希编号，禁止把LLM随手造的英文/SQL词写进资产编码
            hashed_rc = f"{prefix}_{hashlib.md5(str(clean_item.get('title') or clean_item).encode('utf-8')).hexdigest()[:8]}"
            clean_item["rule_code"] = hashed_rc
            by_code[hashed_rc] = clean_item
    return list(by_code.values())


def _extract_group_with_llm(
    banner: str,
    group_evs: List[Any],
    clean_comments: List[str],
    *,
    domain: str,
    code_plan: Optional[Dict[str, Any]],
    stage_meta: Optional[Dict[str, Any]],
    llm: Any,
    bus: Any,
) -> None:
    """按规划好的逻辑阶段带着全局上下文调用受控 LLM 提炼纯业务解释，并回填主实证与元数据。"""
    agg_stmt_kinds = sorted(
        {
            str((ev.internal_locator or {}).get("statement_kind") or "SELECT")
            for ev in group_evs
        }
    )
    total_joins = sum(int((ev.internal_locator or {}).get("join_count") or 0) for ev in group_evs)
    draft_inc: List[Dict[str, Any]] = []
    draft_exc: List[Dict[str, Any]] = []
    draft_br: List[Dict[str, Any]] = []
    seen_rc = set()
    for ev in group_evs:
        for item in ev.inclusion_predicates:
            rc = item.get("rule_code") or item.get("title")
            if rc not in seen_rc:
                seen_rc.add(rc)
                draft_inc.append(item)
        for item in ev.exclusion_predicates:
            rc = item.get("rule_code") or item.get("title")
            if rc not in seen_rc:
                seen_rc.add(rc)
                draft_exc.append(item)
        for item in ev.branch_cases:
            t = item.get("title")
            if t not in seen_rc:
                seen_rc.add(t)
                draft_br.append(item)

    payload: Dict[str, Any] = {
        "business_domain_hint": domain,
        "target_concept_hint": group_evs[0].target_concept_hint,
        "procedure_banner": sanitize_business_text(banner),
        "ast_digest": {
            "step_count": len(group_evs),
            "statement_kinds": agg_stmt_kinds,
            "total_joins": total_joins,
        },
        "business_comments": clean_comments,
        "deterministic_draft": {
            "source_summary": group_evs[0].source_summary,
            "inclusions": draft_inc[:6],
            "exclusions": draft_exc[:6],
            "branches": draft_br[:6],
            "calculation_summary": group_evs[0].calculation_summary,
            "dedup_grain_hint": next((e.dedup_grain_hint for e in group_evs if e.dedup_grain_hint), ""),
        },
    }
    if stage_meta is not None:
        payload["code_plan"] = {
            "business_overview": (code_plan or {}).get("business_overview") or sanitize_business_text(banner),
            "canonical_domain": (code_plan or {}).get("canonical_domain") or domain,
            "stage_title": stage_meta["stage_title"],
            "stage_role": stage_meta["stage_role"],
            "stage_purpose": stage_meta["stage_purpose"],
            "upstream_stages": stage_meta.get("upstream_stages") or [],
        }

    plan_locator_fields = {
        "plan_stage": "PLAN_THEN_EXTRACT" if stage_meta is not None else "SINGLE_STAGE",
        "plan_applied": code_plan is not None,
    }
    if stage_meta is not None:
        plan_locator_fields["stage_title"] = stage_meta["stage_title"]
        plan_locator_fields["stage_role"] = stage_meta["stage_role"]
    if code_plan and code_plan.get("business_overview"):
        plan_locator_fields["business_overview"] = code_plan["business_overview"][:160]

    try:
        args, _trace = call_llm_validated(
            llm,
            "translate_logic_evidence",
            (
                "结合 code_plan 中的整套逻辑业务主线、本阶段职能分工及上游衔接关系，以及 AST 结构摘要与业务注释，"
                "用通俗纯中文还原当前逻辑阶段实际做了什么、包含哪些业务规则与口径解释。"
                "保持 deterministic_draft 已有规则准确度，严禁出现物理表名、字段名、SQL 或脚本名。"
            ),
            payload,
            backoff=(0.2, 0.5),
            bus=bus,
        )
    except BudgetExceededError:
        raise
    except WorkflowError:
        for ev in group_evs:
            ev.internal_locator.update(plan_locator_fields)
        return

    lead = group_evs[0]
    if isinstance(args.get("source_summary"), str) and args["source_summary"].strip():
        lead.source_summary = sanitize_business_text(args["source_summary"])
    if isinstance(args.get("calculation_summary"), str) and args["calculation_summary"].strip():
        if (
            lead.calculation_kind != "CONFLICT"
            or "两种" in args["calculation_summary"]
            or "分歧" in args["calculation_summary"]
            or "并行" in args["calculation_summary"]
        ):
            lead.calculation_summary = sanitize_business_text(args["calculation_summary"])
    if isinstance(args.get("dedup_grain_hint"), str) and args["dedup_grain_hint"].strip() and not lead.dedup_grain_hint:
        lead.dedup_grain_hint = sanitize_business_text(args["dedup_grain_hint"])

    lead.inclusion_predicates = _merge_rule_list(lead.inclusion_predicates, args.get("inclusions"), "LLM_INC")
    lead.exclusion_predicates = _merge_rule_list(lead.exclusion_predicates, args.get("exclusions"), "LLM_EXC")
    if isinstance(args.get("branches"), list) and args["branches"]:
        br_by_title = {b.get("title"): dict(b) for b in lead.branch_cases if b.get("title")}
        for item in args["branches"]:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            clean_b = {k: sanitize_business_text(str(v)) if isinstance(v, str) else v for k, v in item.items()}
            br_by_title[clean_b["title"]] = clean_b
        lead.branch_cases = list(br_by_title.values())

    for ev in group_evs:
        ev.internal_locator.update(plan_locator_fields)
        ev.internal_locator["ai_refined"] = True


def plan_and_extract_script_evidences(
    filename: str,
    banner: str,
    evidences: List[Any],
    comments_pool: List[str],
    *,
    is_sac_script: bool,
    llm: Any,
    bus: Any,
) -> List[Any]:
    """多步 SQL Plan-then-Extract：先由 AI 看全局步骤 DAG 分阶段规划，再带阶段上下文逐段提取纯业务实证。"""
    if llm is None or not evidences:
        return evidences

    clean_comments = [
        sanitize_business_text(c)
        for c in dict.fromkeys(comments_pool)
        if c and not detect_technical_jargon(sanitize_business_text(c))
    ][:10]

    upstream_map = _compute_upstream_step_map(evidences)
    code_plan, groups = _plan_code_structure_with_llm(
        filename,
        banner,
        evidences,
        comments_pool,
        is_sac_script=is_sac_script,
        llm=llm,
        bus=bus,
    )

    step_to_stage = (code_plan or {}).get("step_to_stage") or {}
    if not step_to_stage:
        step_to_stage = {idx: g["stage_title"] for g in groups for idx in g["step_indices"]}

    active_indices: List[int] = []
    for g in groups:
        if g["stage_role"] == "IGNORE":
            continue
        group_indices = g["step_indices"]
        group_set = set(group_indices)
        upstream_stages = list(
            dict.fromkeys(
                step_to_stage[u]
                for i in group_indices
                for u in upstream_map.get(i, [])
                if u not in group_set and u in step_to_stage
            )
        )
        group_evs = [evidences[i] for i in group_indices]
        for ev in group_evs:
            if not is_sac_script or ev.business_domain_hint in {"", "核心业务规则", "核心业务规则与逻辑加工", "通用业务规范"}:
                ev.business_domain_hint = g["business_domain"]
        _extract_group_with_llm(
            banner,
            group_evs,
            clean_comments,
            domain=g["business_domain"],
            code_plan=code_plan,
            stage_meta={**g, "upstream_stages": upstream_stages},
            llm=llm,
            bus=bus,
        )
        active_indices.extend(group_indices)

    active_set = set(active_indices)
    return [evidences[i] for i in range(len(evidences)) if i in active_set]


def enrich_script_evidences_with_llm(
    filename: str,
    banner: str,
    evidences: List[Any],
    comments_pool: List[str],
    *,
    llm: Any,
    bus: Any,
) -> None:
    """单步或按域粒度调用受控大模型深度翻译并润色实证规则与业务解释。"""
    if llm is None or not evidences:
        return

    grouped: Dict[str, List[Any]] = {}
    for ev in evidences:
        grouped.setdefault(ev.business_domain_hint, []).append(ev)

    clean_comments = [
        sanitize_business_text(c)
        for c in dict.fromkeys(comments_pool)
        if c and not detect_technical_jargon(sanitize_business_text(c))
    ][:10]

    for domain, group in grouped.items():
        _extract_group_with_llm(
            banner,
            group,
            clean_comments,
            domain=domain,
            code_plan=None,
            stage_meta=None,
            llm=llm,
            bus=bus,
        )

