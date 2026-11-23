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
from typing import Any, Dict, List, Optional

import sqlglot
from sqlglot import exp

from app.infra.llm import call_llm_validated
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
        c = re.sub(r"^(?:任务描述|程序描述|程序名称|业务说明|功能说明)[：:\s]*", "", raw).strip()
        c = sanitize_business_text(c)
        c = re.sub(r"[（(][^）)]*[）)]", "", c).strip()
        if len(c) >= 4 and not detect_technical_jargon(c):
            return c[:28]
    stem = sanitize_business_text(re.sub(r"\.[A-Za-z0-9]+$", "", filename).replace("_", " "))
    if any("\u4e00" <= ch <= "\u9fff" for ch in stem) and not detect_technical_jargon(stem):
        return stem[:24]
    return "通用业务数据处理域"


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
