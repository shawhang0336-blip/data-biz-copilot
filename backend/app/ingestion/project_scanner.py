"""Phase 0 零大模型静态预扫描、跨脚本表级血缘 DAG 与三层智能分流器（接入层 Layer 3）。

职责（全程 0 LLM 调用）：
1. 并行提取每份 SQL / 存储过程脚本的读写表名集合、AST 结构复杂度、中文注释密度与监管体系信号；
2. 计算归一化 AST 模板指纹 `ast_template_hash`，自动聚簇分行副本/多年度拷贝等同构脚本；
3. 构建跨脚本「写入表 -> 读取表」血缘 DAG 与 Kahn 拓扑序（含环路安全断环）；
4. 透明可解释地划分三层优先级：
   - `TIER_3_SKIP_AI`：备份/废弃文件、纯 DDL/日志运维、无过滤无聚合纯 ODS 透传、同构模板克隆、孤立中间加工；
   - `TIER_1_CORE`：命中监管体系/报送终表命名或 DAG 终端叶子且含实质业务规则；
   - `TIER_2_UPSTREAM`：1–3 跳内喂养 Tier 1 且包含实质过滤/分支/聚合逻辑的上游加工脚本。
"""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
import hashlib
from pathlib import PurePath
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.infra.retrieval import infer_regulatory_regime
from app.ingestion.ast_extractor import extract_sql_ast_skeleton
from app.ingestion.code_parser import (
    _extract_comments,
    _extract_procedure_banner,
    _is_housekeeping_chunk,
    _is_known_sac_logic,
    _unwrap_execute_immediate,
    split_sql_chunks,
)

TIER_1_CORE = "TIER_1_CORE"
TIER_2_UPSTREAM = "TIER_2_UPSTREAM"
TIER_3_SKIP_AI = "TIER_3_SKIP_AI"

_BACKUP_DIR_RE = re.compile(
    r"(?:^|[/\\])(?:bak|backup|backups|old|archive|history|deprecated|trash)(?:[/\\]|$)",
    re.IGNORECASE,
)
_BACKUP_STEM_RE = re.compile(
    r"(?:_bak|_old|_backup|_copy|_del|_drop|_deprecated|_test|_20\d{6}|_v\d+)(?:$)",
    re.IGNORECASE,
)
_LOG_TABLE_RE = re.compile(
    r"(?:^|_)(?:LOG|AUDIT_LOG|ERR_LOG|ERROR_LOG|JOB_LOG|ETL_LOG|RUN_LOG|BATCH_LOG|SP_LOG|EXCEPTION_LOG|CTL|CTRL|SEQ)(?:_|$)",
    re.IGNORECASE,
)
_ODS_TABLE_RE = re.compile(r"^(?:ODS|STG|SRC|RAW|LANDING|BDM_RAW)_", re.IGNORECASE)
_REPORT_TARGET_RE = re.compile(
    r"^(?:RPT|ADS|DM|DWS_RPT|SF_|EAST_|SAFE_|PLUS_|YBT_|REP_|REPORT_|(?:G|S|GF|SF)\d{2})",
    re.IGNORECASE,
)
_REPORT_PATH_RE = re.compile(
    r"(?:1104|EAST|SAFE|FAL|BOP|PLUS|SAC|一表通|报送|监管|RPT|ADS|DM|(?:G|S|GF|SF)\d{2})",
    re.IGNORECASE,
)
_INTERMEDIATE_TARGET_RE = re.compile(
    r"^(?:TMP|TEMP|WRK|WORK|MID|STAGE|STG|DWD|ODS|PREP|BUF)_",
    re.IGNORECASE,
)

# 仅剥离常见分区/分行/年份后缀用于模板聚簇，保留核心业务表名以免误合并不同报表
_BRANCH_PART_SUFFIX_RE = re.compile(
    r"_(?:bj|sh|gz|sz|hz|nj|cd|wh|cq|tj|xa|xm|nb|qd|dl|branch_?\d+|br_?\d+|org_?\d+|prov_?\d+|area_?\d+|20\d{2}(?:\d{2,4})?)\b",
    re.IGNORECASE,
)
_TEMPLATE_STR_LIT_RE = re.compile(r"'(?:[^']|'')*'")
_TEMPLATE_DATE_RE = re.compile(r"\b20\d{2}[-/]?\d{2}(?:[-/]?\d{2})?\b")
_TEMPLATE_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

_DML_WRITE_KINDS = {"INSERT", "MERGE", "UPDATE", "CTAS"}


def normalize_table_identifier(raw: str) -> str:
    """剥离 Schema/库前缀并统一大写，便于跨脚本精准连线。"""
    txt = str(raw or "").replace('"', "").replace("`", "").strip().upper()
    if not txt:
        return ""
    if "." in txt:
        txt = txt.rsplit(".", 1)[-1].strip()
    if txt in {"DUAL", "SELECT", "SET", "VALUES", "WHERE"}:
        return ""
    return txt


def _regex_fallback_tables(stmt: str) -> Tuple[List[str], List[str], str]:
    """当 sqlglot 遇到非常见方言或 PL/SQL 包裹未能构树时，确定性正则提取读写表与语句类型。"""
    upper = stmt.upper()
    kind = "UNKNOWN"
    for candidate in ("MERGE", "INSERT", "UPDATE", "CREATE", "DELETE", "TRUNCATE", "GRANT", "ALTER", "SELECT"):
        if re.search(rf"\b{candidate}\b", upper):
            kind = candidate
            break
    if kind == "CREATE" and re.search(r"\bCREATE\b.*\bAS\s+(?:WITH\b|SELECT\b)", upper, flags=re.DOTALL):
        kind = "CTAS"

    targets: List[str] = []
    sources: List[str] = []
    for pat in (
        r"\bINSERT\s+(?:INTO|OVERWRITE\s+TABLE)\s+\"?([A-Za-z0-9_$.\"`]+)\"?",
        r"\bMERGE\s+INTO\s+\"?([A-Za-z0-9_$.\"`]+)\"?",
        r"\bUPDATE\s+\"?([A-Za-z0-9_$.\"`]+)\"?",
        r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([A-Za-z0-9_$.\"`]+)\"?",
    ):
        for m in re.finditer(pat, stmt, flags=re.IGNORECASE):
            name = normalize_table_identifier(m.group(1))
            if name and name not in targets:
                targets.append(name)

    for pat in (
        r"\b(?:FROM|JOIN|USING)\s+\"?([A-Za-z0-9_$.\"`]+)\"?",
    ):
        for m in re.finditer(pat, stmt, flags=re.IGNORECASE):
            name = normalize_table_identifier(m.group(1))
            if name and name not in targets and name not in sources:
                sources.append(name)

    return targets, sources, kind


def compute_ast_template_hash(sql_text: str) -> str:
    """生成归一化 SQL 结构指纹：剥离注释并折叠字面量与分行/年份分区后缀，用于同构副本零模型复用。"""
    unwrapped = _unwrap_execute_immediate(sql_text or "")
    chunks = split_sql_chunks(unwrapped)
    norm_chunks: List[str] = []
    for _line, stmt in chunks:
        cleaned = re.sub(r"/\*.*?\*/", " ", stmt, flags=re.DOTALL)
        cleaned = re.sub(r"--[^\n]*", " ", cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            continue
        upper = cleaned.upper()
        if not any(kw in upper for kw in ("INSERT", "MERGE", "UPDATE", "SELECT", "CREATE", "DELETE")):
            continue
        if _is_housekeeping_chunk(cleaned, []):
            continue
        cleaned = _TEMPLATE_STR_LIT_RE.sub("'?'", cleaned)
        cleaned = _TEMPLATE_DATE_RE.sub("DATE_LIT", cleaned)
        cleaned = _TEMPLATE_NUM_RE.sub("NUM_LIT", cleaned)
        cleaned = _BRANCH_PART_SUFFIX_RE.sub("_PART", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
        if cleaned:
            norm_chunks.append(cleaned)
    if not norm_chunks:
        return ""
    canonical = f"n={len(norm_chunks)}||" + ";;".join(norm_chunks)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ScriptScanProfile:
    material_id: str
    title: str
    relative_path: str = ""
    target_tables: List[str] = field(default_factory=list)
    source_tables: List[str] = field(default_factory=list)
    statement_kinds: List[str] = field(default_factory=list)
    chunk_count: int = 0
    join_count: int = 0
    has_where: bool = False
    has_case: bool = False
    has_aggregation: bool = False
    has_window: bool = False
    has_merge: bool = False
    has_dml_write: bool = False
    is_sac_script: bool = False
    cn_comment_chars: int = 0
    inferred_regime: str = "通用监管"
    ast_template_hash: str = ""
    business_density_score: float = 0.0
    tier: str = TIER_1_CORE
    skip_reason: str = ""
    template_master_id: Optional[str] = None
    upstream_ids: List[str] = field(default_factory=list)
    downstream_ids: List[str] = field(default_factory=list)
    topo_order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def scan_single_script(
    material_id: str,
    title: str,
    sql_text: str,
    *,
    relative_path: str = "",
) -> ScriptScanProfile:
    """零 LLM 提取单脚本 AST 骨架画像、读写表集合、业务密度评分与模板结构指纹。"""
    text = sql_text or ""
    banner = _extract_procedure_banner(text)
    inferred_regime = infer_regulatory_regime(relative_path or title, banner, text[:2000])
    chunks = split_sql_chunks(text)

    target_tables: List[str] = []
    source_tables: List[str] = []
    statement_kinds: List[str] = []
    join_count = 0
    has_where = False
    has_case = False
    has_aggregation = False
    has_window = False
    has_merge = False
    has_dml_write = False
    is_sac = False
    active_chunks = 0
    all_comments: List[str] = []

    for _start_line, stmt in chunks:
        upper = stmt.upper()
        if not any(kw in upper for kw in ("INSERT", "MERGE", "UPDATE", "SELECT", "CREATE", "DELETE", "TRUNCATE", "GRANT", "ALTER")):
            continue
        comments = _extract_comments(stmt)
        all_comments.extend(comments)

        fb_targets, fb_sources, fb_kind = _regex_fallback_tables(stmt)
        if _is_housekeeping_chunk(stmt, fb_targets):
            continue

        active_chunks += 1
        sk = extract_sql_ast_skeleton(stmt)
        stmt_kind = str(sk.get("statement_kind") or "UNKNOWN").upper()
        if stmt_kind == "CREATE" and fb_kind == "CTAS":
            stmt_kind = "CTAS"
        elif stmt_kind == "UNKNOWN" and fb_kind != "UNKNOWN":
            stmt_kind = fb_kind

        if stmt_kind not in statement_kinds:
            statement_kinds.append(stmt_kind)
        if stmt_kind in _DML_WRITE_KINDS:
            has_dml_write = True
        if stmt_kind == "MERGE":
            has_merge = True

        ast_tgts = [normalize_table_identifier(t) for t in (sk.get("target_tables") or [])]
        ast_srcs = [normalize_table_identifier(t) for t in (sk.get("source_tables") or [])]
        for t in (ast_tgts if ast_tgts else fb_targets):
            if t and t not in target_tables:
                target_tables.append(t)
        for s in (ast_srcs if ast_srcs else fb_sources):
            if s and s not in source_tables:
                source_tables.append(s)

        join_count += int(sk.get("join_count") or 0)
        if not sk.get("ast_parsed"):
            join_count += len(re.findall(r"\bJOIN\b", upper))

        logic_details = sk.get("logic_details") or {}
        if logic_details.get("where_predicates") or logic_details.get("having_predicates") or re.search(r"\bWHERE\b", upper):
            has_where = True
        if (sk.get("case_branch_count") or 0) > 0 or re.search(r"\bCASE\s+WHEN\b", upper):
            has_case = True
        if (sk.get("aggregate_funcs") or []) or (sk.get("group_by_count") or 0) > 0 or sk.get("has_arithmetic"):
            has_aggregation = True
        if sk.get("has_window_dedup") or logic_details.get("window_specs") or re.search(r"\bOVER\s*\(", upper):
            has_window = True

        if not is_sac and _is_known_sac_logic(title, banner, comments, target_tables + source_tables, stmt):
            is_sac = True

    # 自表读不作为外部上游来源表（常见于 MERGE / 重跑 UPDATE 同表）
    tgt_set = set(target_tables)
    clean_sources = [s for s in source_tables if s not in tgt_set]

    cn_chars = sum(len(re.findall(r"[\u4e00-\u9fff]", c)) for c in ([banner] + all_comments))
    density = (
        (2.0 if has_where else 0.0)
        + (2.5 if has_case else 0.0)
        + (2.5 if has_aggregation else 0.0)
        + (2.0 if has_window else 0.0)
        + (2.0 if has_merge else 0.0)
        + min(3.0, join_count * 0.8)
        + (3.0 if inferred_regime != "通用监管" else 0.0)
        + min(3.0, cn_chars / 25.0)
    )

    return ScriptScanProfile(
        material_id=material_id,
        title=title,
        relative_path=relative_path or title,
        target_tables=target_tables,
        source_tables=clean_sources,
        statement_kinds=statement_kinds,
        chunk_count=active_chunks,
        join_count=join_count,
        has_where=has_where,
        has_case=has_case,
        has_aggregation=has_aggregation,
        has_window=has_window,
        has_merge=has_merge,
        has_dml_write=has_dml_write,
        is_sac_script=is_sac,
        cn_comment_chars=cn_chars,
        inferred_regime=inferred_regime,
        ast_template_hash=compute_ast_template_hash(text),
        business_density_score=round(density, 2),
    )


def build_lineage_dag(profiles: List[ScriptScanProfile]) -> Dict[str, Any]:
    """依据标准化 target_tables -> source_tables 构建跨脚本血缘 DAG 与无死循环 Kahn 拓扑序。"""
    by_id: Dict[str, ScriptScanProfile] = {p.material_id: p for p in profiles}
    writers: Dict[str, List[str]] = {}
    for p in profiles:
        p.upstream_ids = []
        p.downstream_ids = []
        for tbl in p.target_tables:
            writers.setdefault(tbl, [])
            if p.material_id not in writers[tbl]:
                writers[tbl].append(p.material_id)

    edges: List[Tuple[str, str]] = []
    seen_edges = set()
    for p in profiles:
        for src_tbl in p.source_tables:
            for up_id in writers.get(src_tbl, []):
                if up_id == p.material_id:
                    continue
                if (up_id, p.material_id) not in seen_edges:
                    seen_edges.add((up_id, p.material_id))
                    edges.append((up_id, p.material_id))
                    p.upstream_ids.append(up_id)
                    by_id[up_id].downstream_ids.append(p.material_id)

    in_deg: Dict[str, int] = {p.material_id: len(p.upstream_ids) for p in profiles}
    ready = deque(
        sorted(
            [pid for pid, deg in in_deg.items() if deg == 0],
            key=lambda pid: (-by_id[pid].business_density_score, by_id[pid].title),
        )
    )
    remaining = set(by_id.keys())
    order_list: List[str] = []

    while remaining:
        while ready:
            cur = ready.popleft()
            if cur not in remaining:
                continue
            remaining.remove(cur)
            order_list.append(cur)
            for nxt in by_id[cur].downstream_ids:
                if nxt in remaining:
                    in_deg[nxt] -= 1
                    if in_deg[nxt] <= 0:
                        ready.append(nxt)
        if remaining:
            # 遇到互写环路时按入度最小、业务密度从高到低安全断环，确保始终产生全序
            pick = min(
                remaining,
                key=lambda pid: (in_deg[pid], -by_id[pid].business_density_score, by_id[pid].title),
            )
            ready.append(pick)

    for idx, pid in enumerate(order_list):
        by_id[pid].topo_order = idx

    return {"edges": edges, "topo_order": order_list}


def _has_substantive_logic(p: ScriptScanProfile) -> bool:
    return bool(
        p.has_where
        or p.has_case
        or p.has_aggregation
        or p.has_window
        or p.has_merge
        or p.join_count > 0
    )


def _is_backup_script(p: ScriptScanProfile) -> bool:
    rel = p.relative_path or p.title
    if _BACKUP_DIR_RE.search(rel):
        return True
    stem = PurePath(p.title).stem
    if _BACKUP_STEM_RE.search(stem):
        return True
    return False


def _pick_canonical_master(cluster: List[ScriptScanProfile]) -> ScriptScanProfile:
    return max(
        cluster,
        key=lambda p: (
            1 if p.inferred_regime != "通用监管" else 0,
            p.cn_comment_chars,
            p.business_density_score,
            -len(p.title),
            p.title,
        ),
    )


def classify_project_scripts(
    profiles: List[ScriptScanProfile],
    *,
    enable_template_dedup: bool = True,
) -> List[ScriptScanProfile]:
    """对脚本执行四步透明分层，区分核心报送(Tier 1)、上游关键加工(Tier 2)与免AI跳过(Tier 3)。"""
    by_id: Dict[str, ScriptScanProfile] = {p.material_id: p for p in profiles}

    # Pass 1: 硬性 Tier 3 跳过规则（备份、纯 DDL、日志表、纯 ODS 透传）
    candidates: List[ScriptScanProfile] = []
    for p in profiles:
        p.template_master_id = None
        if _is_backup_script(p):
            p.tier = TIER_3_SKIP_AI
            p.skip_reason = "BACKUP_FILE"
            continue
        if not p.has_dml_write:
            p.tier = TIER_3_SKIP_AI
            p.skip_reason = "PURE_DDL"
            continue
        if p.target_tables and all(_LOG_TABLE_RE.search(t) for t in p.target_tables) and p.inferred_regime == "通用监管":
            p.tier = TIER_3_SKIP_AI
            p.skip_reason = "HOUSEKEEPING_LOG"
            continue
        substantive = _has_substantive_logic(p)
        all_ods_target = bool(p.target_tables) and all(_ODS_TABLE_RE.match(t) for t in p.target_tables)
        if not substantive and (all_ods_target or (p.cn_comment_chars < 15 and p.inferred_regime == "通用监管")):
            p.tier = TIER_3_SKIP_AI
            p.skip_reason = "ODS_PASSTHROUGH"
            continue
        p.skip_reason = ""
        candidates.append(p)

    # Pass 2: AST 结构指纹聚簇，自动折叠同构分支/年份副本
    active_after_dedup: List[ScriptScanProfile] = []
    if enable_template_dedup:
        clusters: Dict[str, List[ScriptScanProfile]] = {}
        for p in candidates:
            if p.ast_template_hash:
                clusters.setdefault(p.ast_template_hash, []).append(p)
            else:
                active_after_dedup.append(p)
        for _h, cluster in clusters.items():
            if len(cluster) == 1:
                active_after_dedup.append(cluster[0])
                continue
            master = _pick_canonical_master(cluster)
            active_after_dedup.append(master)
            for sib in cluster:
                if sib.material_id == master.material_id:
                    continue
                sib.tier = TIER_3_SKIP_AI
                sib.skip_reason = "TEMPLATE_CLONE"
                sib.template_master_id = master.material_id
    else:
        active_after_dedup = list(candidates)

    active_ids = {p.material_id for p in active_after_dedup}
    # 将跨脚本边映射到去重后的规范主脚本，避免主从选择不一致导致上下游断链
    canonical_of: Dict[str, str] = {
        p.material_id: (
            p.template_master_id
            if (p.template_master_id and p.template_master_id in active_ids)
            else p.material_id
        )
        for p in profiles
    }
    active_upstream: Dict[str, List[str]] = {pid: [] for pid in active_ids}
    active_downstream: Dict[str, List[str]] = {pid: [] for pid in active_ids}
    for p in active_after_dedup:
        for d in p.downstream_ids:
            cd = canonical_of.get(d, d)
            if cd in active_ids and cd != p.material_id and cd not in active_downstream[p.material_id]:
                active_downstream[p.material_id].append(cd)
                if p.material_id not in active_upstream[cd]:
                    active_upstream[cd].append(p.material_id)
    for sib in candidates:
        if sib.template_master_id and sib.template_master_id in active_ids:
            m_id = sib.template_master_id
            for u in sib.upstream_ids:
                cu = canonical_of.get(u, u)
                if cu in active_ids and cu != m_id and cu not in active_upstream[m_id]:
                    active_upstream[m_id].append(cu)
                    if m_id not in active_downstream[cu]:
                        active_downstream[cu].append(m_id)

    # Pass 3: 识别 Tier 1 核心报送终表与强业务规则叶子脚本
    tier1_ids = set()
    for p in active_after_dedup:
        has_report_target = any(_REPORT_TARGET_RE.match(t) for t in p.target_tables)
        has_report_cue = bool(_REPORT_PATH_RE.search(p.relative_path or p.title)) or p.inferred_regime != "通用监管"
        all_intermediate_target = bool(p.target_tables) and all(_INTERMEDIATE_TARGET_RE.match(t) for t in p.target_tables)
        is_leaf = len(active_downstream[p.material_id]) == 0
        if has_report_target or (has_report_cue and (is_leaf or p.is_sac_script or p.business_density_score >= 4.0)):
            p.tier = TIER_1_CORE
            tier1_ids.add(p.material_id)
        elif is_leaf and not all_intermediate_target and (_has_substantive_logic(p) or p.cn_comment_chars >= 10):
            p.tier = TIER_1_CORE
            tier1_ids.add(p.material_id)

    # 小规模批次兜底：若活动脚本全被中间状态拦截导致无 Tier 1，择优提拔末端脚本为 Tier 1
    if active_after_dedup and not tier1_ids:
        leaves = [p for p in active_after_dedup if len(active_downstream[p.material_id]) == 0] or active_after_dedup
        for p in sorted(leaves, key=lambda x: (-x.business_density_score, x.title))[: max(1, len(leaves))]:
            p.tier = TIER_1_CORE
            tier1_ids.add(p.material_id)

    # Pass 4: 反向可达 1-3 跳识别 Tier 2 上游关键加工；其余孤立中间过程归入 Tier 3 防污染版图
    tier2_reachable = set()
    bfs = deque((tid, 0) for tid in tier1_ids)
    visited = set(tier1_ids)
    while bfs:
        cur_id, depth = bfs.popleft()
        if depth >= 3:
            continue
        for up_id in active_upstream.get(cur_id, []):
            if up_id not in active_ids or up_id in visited:
                continue
            visited.add(up_id)
            tier2_reachable.add(up_id)
            bfs.append((up_id, depth + 1))

    for p in active_after_dedup:
        if p.material_id in tier1_ids:
            continue
        if p.material_id in tier2_reachable and (_has_substantive_logic(p) or p.cn_comment_chars >= 20):
            p.tier = TIER_2_UPSTREAM
            p.skip_reason = ""
        else:
            p.tier = TIER_3_SKIP_AI
            p.skip_reason = "ORPHAN_INTERMEDIATE"

    return profiles


def scan_project_batch(
    scripts: Sequence[Tuple[str, str, str, str]],
    *,
    enable_template_dedup: bool = True,
    max_workers: int = 4,
) -> Dict[str, Any]:
    """并行扫描批量 SQL 脚本 `(material_id, title, relative_path, sql_text)`，返回画像与血缘 DAG 摘要。"""
    if not scripts:
        return {
            "profiles": [],
            "dag_edges": [],
            "upstream_map": {},
            "summary": {
                "total": 0,
                "tier1_count": 0,
                "tier2_count": 0,
                "tier3_count": 0,
                "template_clone_count": 0,
                "skip_reasons": {},
            },
        }

    workers = max(1, min(int(max_workers or 4), len(scripts), 8))
    profiles: List[ScriptScanProfile] = []
    if workers == 1 or len(scripts) <= 4:
        for mid, title, rel_path, sql_text in scripts:
            profiles.append(scan_single_script(mid, title, sql_text, relative_path=rel_path))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [
                pool.submit(scan_single_script, mid, title, sql_text, relative_path=rel_path)
                for mid, title, rel_path, sql_text in scripts
            ]
            for fut in futs:
                profiles.append(fut.result())

    dag = build_lineage_dag(profiles)
    classify_project_scripts(profiles, enable_template_dedup=enable_template_dedup)
    # 按拓扑序排列，确保后续波次严格先上游后下游
    profiles.sort(key=lambda p: p.topo_order)

    skip_reasons: Dict[str, int] = {}
    for p in profiles:
        if p.skip_reason:
            skip_reasons[p.skip_reason] = skip_reasons.get(p.skip_reason, 0) + 1

    tier1_cnt = sum(1 for p in profiles if p.tier == TIER_1_CORE)
    tier2_cnt = sum(1 for p in profiles if p.tier == TIER_2_UPSTREAM)
    tier3_cnt = sum(1 for p in profiles if p.tier == TIER_3_SKIP_AI)
    clone_cnt = sum(1 for p in profiles if p.skip_reason == "TEMPLATE_CLONE")

    return {
        "profiles": profiles,
        "dag_edges": dag["edges"],
        "upstream_map": {p.material_id: list(p.upstream_ids) for p in profiles},
        "summary": {
            "total": len(profiles),
            "tier1_count": tier1_cnt,
            "tier2_count": tier2_cnt,
            "tier3_count": tier3_cnt,
            "template_clone_count": clone_cnt,
            "skip_reasons": skip_reasons,
        },
    }
