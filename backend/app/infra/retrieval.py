"""零外置依赖的纯 Python 多监管体系识别与中文 N-gram + Okapi BM25 混合检索器（基础设施层 Layer 0）。

无需 Elasticsearch、向量数据库或第三方 NLP 分词库，直接基于内存资产列表完成 <3ms 毫秒级召回：
1. `infer_regulatory_regime`：自动识别 1104 / EAST / SAFE / PLUS / 一表通 / 人行统计 / 通用监管；
2. `tokenize_for_search`：过滤高频虚词与口语停用词后提取英文/数字代号与中文 2-gram / 3-gram；
3. `bm25_score_documents`：支持多字段加权、精确短语奖励与监管体系同源加权 / 跨体系降权。
"""

import math
import re
from typing import Dict, List, Optional, Sequence

_REGIME_REPORT_1104_RE = re.compile(r"(?<![A-Za-z0-9])(?:G|S|GF|SF)\d{2}(?![A-Za-z0-9])", re.IGNORECASE)
_PLUS_RE = re.compile(r"(?<![A-Za-z0-9_])PLUS(?![A-Za-z0-9_])", re.IGNORECASE)
_SAFE_RE = re.compile(r"(?<![A-Za-z0-9_])(?:SAFE|FAL|BOP|SAC)(?![A-Za-z0-9_])", re.IGNORECASE)
_EAST_RE = re.compile(r"(?<![A-Za-z0-9_])EAST(?:\s*5\.0|5|4)?(?![A-Za-z0-9_])", re.IGNORECASE)

_STOP_WORDS = (
    "为什么", "是什么", "有没有", "怎么算", "怎么样", "有哪些", "哪几个",
    "目前", "当前", "现在", "请问", "我们", "系统", "逻辑", "里面", "已经",
    "关于", "一下", "这块", "那个", "可以", "是否", "规则", "问题", "情况",
    "方式", "处理", "业务", "数据", "统计", "指标", "口径", "包括", "包含",
    "以及", "或者", "还有", "什么", "怎么", "如何", "哪些",
)
_SINGLE_STOP_CHARS = set("的了吗呢是在和与对把有被给按里中上下哪些")
_ALNUM_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)
_CN_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_CN_UNIT_RE = re.compile(r"\d+\s*[万亿元笔家户天月年日%]")


def infer_regulatory_regime(*texts: str) -> str:
    """识别文本或文件名归属的金融监管报送体系。"""
    blob = " ".join(str(t or "") for t in texts).strip()
    if not blob:
        return "通用监管"
    if "1104" in blob or "非现场监管" in blob or _REGIME_REPORT_1104_RE.search(blob):
        return "1104"
    if "一表通" in blob:
        return "一表通"
    if _PLUS_RE.search(blob) or any(k in blob for k in ("PLUS报送", "PLUS平台", "手工补录平台", "手工补录")):
        return "PLUS"
    if _SAFE_RE.search(blob) or any(
        k in blob
        for k in (
            "国家外汇管理局",
            "外管",
            "国际收支",
            "资本项目",
            "涉外收支",
            "结售汇",
            "大额与可疑外汇",
            "应报未报",
        )
    ):
        return "SAFE"
    if _EAST_RE.search(blob) or any(
        k in blob
        for k in (
            "银行业监管数据标准化",
            "个人存款分户账",
            "对公客户信息",
            "银行机构信息",
            "综合授信额度",
        )
    ):
        return "EAST"
    if any(
        k in blob
        for k in (
            "人行大集中",
            "金融基础数据",
            "中国人民银行",
            "大额交易和可疑交易",
            "数字人民币",
            "征信",
            "金标",
        )
    ):
        return "人行统计"
    return "通用监管"


def _strip_filler(text: str) -> str:
    cleaned = text
    for w in _STOP_WORDS:
        cleaned = cleaned.replace(w, " ")
    # 仅按高频纯虚词/连词断句，严禁误删“对公”“中资”“线上”等实义前缀字
    cleaned = re.sub(r"[的了吗呢与和及或]+", " ", cleaned)
    return cleaned


def tokenize_for_search(text: str) -> List[str]:
    """混合提取英文/数字代号、数量单位词及中文 2-gram / 3-gram 检索特征词项。"""
    raw = (text or "").strip()
    if not raw:
        return []
    tokens: List[str] = []

    for unit in _CN_UNIT_RE.findall(raw):
        norm = re.sub(r"\s+", "", unit)
        if norm:
            tokens.append(norm)

    for code in _ALNUM_RE.findall(raw):
        tokens.append(code.lower())

    cleaned = _strip_filler(raw)
    for run in _CN_RUN_RE.findall(cleaned):
        stripped = run.strip()
        if len(stripped) < 2:
            continue
        if len(stripped) <= 3:
            tokens.append(stripped)
        if len(stripped) == 2:
            continue
        for i in range(len(stripped) - 1):
            tokens.append(stripped[i : i + 2])
        for i in range(len(stripped) - 2):
            tokens.append(stripped[i : i + 3])
    return tokens


def _unique_query_phrases(query: str) -> List[str]:
    raw = (query or "").strip()
    if not raw:
        return []
    phrases: List[str] = []
    for unit in _CN_UNIT_RE.findall(raw):
        norm = re.sub(r"\s+", "", unit)
        if norm and norm not in phrases:
            phrases.append(norm)
    for code in _ALNUM_RE.findall(raw):
        lc = code.lower()
        if lc not in phrases:
            phrases.append(lc)
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", _strip_filler(raw)):
        stripped = seg.strip()
        if len(stripped) >= 2 and stripped not in phrases:
            phrases.append(stripped)
    return phrases


def bm25_score_documents(
    query: str,
    docs: Sequence[Dict[str, str]],
    *,
    field_weights: Dict[str, float],
    regimes: Optional[Sequence[str]] = None,
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    """多字段加权 Okapi BM25 检索打分（附带短语精确奖励与监管体系上下文调节）。"""
    n_docs = len(docs)
    if n_docs == 0:
        return []

    q_tokens = tokenize_for_search(query)
    q_unique = list(dict.fromkeys(q_tokens))
    q_phrases = _unique_query_phrases(query)
    q_regime = infer_regulatory_regime(query)

    if not q_unique and q_regime == "通用监管":
        return [0.0] * n_docs

    fields = list(field_weights.keys())
    field_tokens: Dict[str, List[List[str]]] = {f: [] for f in fields}
    field_tf: Dict[str, List[Dict[str, int]]] = {f: [] for f in fields}
    field_lens: Dict[str, List[int]] = {f: [] for f in fields}
    field_avg_len: Dict[str, float] = {}
    doc_freq: Dict[str, int] = {}

    for d_idx, doc in enumerate(docs):
        seen_in_doc = set()
        for f in fields:
            toks = tokenize_for_search(doc.get(f, ""))
            field_tokens[f].append(toks)
            field_lens[f].append(len(toks))
            tf_map: Dict[str, int] = {}
            for t in toks:
                tf_map[t] = tf_map.get(t, 0) + 1
                seen_in_doc.add(t)
            field_tf[f].append(tf_map)
        for t in seen_in_doc:
            doc_freq[t] = doc_freq.get(t, 0) + 1

    for f in fields:
        total_len = sum(field_lens[f])
        field_avg_len[f] = (total_len / n_docs) if total_len > 0 else 1.0

    idf_map: Dict[str, float] = {
        t: math.log(1.0 + (n_docs - doc_freq.get(t, 0) + 0.5) / (doc_freq.get(t, 0) + 0.5))
        for t in q_unique
    }

    primary_field = fields[0] if fields else ""
    scores: List[float] = []
    q_clean = re.sub(r"\s+", "", query or "")

    for d_idx, doc in enumerate(docs):
        bm25_sum = 0.0
        matched_tokens = set()
        for f, weight in field_weights.items():
            tf_map = field_tf[f][d_idx]
            dl = field_lens[f][d_idx]
            if dl == 0:
                continue
            avg_dl = field_avg_len[f] or 1.0
            norm_denom = k1 * (1.0 - b + b * (dl / avg_dl))
            f_score = 0.0
            for t in q_unique:
                tf = tf_map.get(t, 0)
                if tf <= 0:
                    continue
                matched_tokens.add(t)
                f_score += idf_map[t] * ((tf * (k1 + 1.0)) / (tf + norm_denom))
            bm25_sum += weight * f_score

        doc_regime = regimes[d_idx] if (regimes is not None and d_idx < len(regimes)) else infer_regulatory_regime(
            doc.get("regime", ""),
            doc.get(primary_field, ""),
        )
        # 若零词项命中且非明确的监管体系名查询，直接归零避免无关噪音（如“期货期权强平”）误召回
        explicit_regime_match = q_regime != "通用监管" and doc_regime == q_regime
        if not matched_tokens and not explicit_regime_match:
            scores.append(0.0)
            continue

        hay = " ".join(str(doc.get(f, "") or "") for f in fields)
        hay_lower = hay.lower()
        primary_txt = str(doc.get(primary_field, "") or "").lower()

        phrase_bonus = 0.0
        verbatim_hits = 0
        if len(q_clean) >= 2 and q_clean.lower() in hay_lower:
            verbatim_hits += 1
            phrase_bonus += 6.0 if q_clean.lower() in primary_txt else 3.5
        for p in q_phrases:
            if len(p) >= 2 and p.lower() in hay_lower:
                verbatim_hits += 1
                phrase_bonus += 2.5 if p.lower() in primary_txt else 1.0

        # 多词复杂问题若无任何短语直中且仅偶发命中极少数孤立二字切片（如“保证金”里的“保证”），视为无关噪音归零
        matched_ratio = len(matched_tokens) / max(1, len(q_unique))
        if (
            verbatim_hits == 0
            and not explicit_regime_match
            and len(q_unique) >= 4
            and (len(matched_tokens) < 2 or matched_ratio < 0.12)
        ):
            scores.append(0.0)
            continue

        total = bm25_sum + phrase_bonus
        if q_regime != "通用监管":
            if doc_regime == q_regime:
                total = total * 1.45 + 3.0
            elif doc_regime != "通用监管":
                total = total * 0.55

        scores.append(round(total, 4))

    return scores
