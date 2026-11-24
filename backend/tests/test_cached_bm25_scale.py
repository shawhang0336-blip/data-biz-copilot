"""万级规则规模下 CachedBM25Index 检索性能、打分精度与缓存失效一致性测试。"""

import time

from app.catalog.mutations import confirm_business_rule
from app.catalog.synthesizer import load_builtin_sample_pack
from app.catalog.views import get_catalog_cache_signature, list_business_rules
from app.infra.config import Settings
from app.infra.retrieval import (
    CachedBM25Index,
    bm25_score_documents,
    get_or_build_bm25_index,
    invalidate_bm25_caches,
)

from .helpers import make_session


def test_cached_bm25_matches_exact_scores_and_scales_to_10k_rules():
    domains = [
        ("1104 G01 个人存款监管报送", "1104"),
        ("EAST 对公信贷分户账", "EAST"),
        ("SAFE 大额与可疑外汇交易", "SAFE"),
        ("PLUS 手工补录平台监测", "PLUS"),
        ("一表通跨条线资产负债核验", "一表通"),
    ]
    topics = [
        ("质押存单扣减与净额计算", "对正常存续个人定期存单质押担保部分予以扣减后核算净额"),
        ("休眠及已销户账户自动剔除", "连续三年无动账或已办结销户手续的账户自动剔除出常规报送"),
        ("境内与境外居民客户识别", "区分境内企业与境外离岸机构执行不同外币折算路径"),
        ("柜面与网银双渠道交易去重", "同一客户同一交易日跨柜面网银重复流水仅保留唯一最新状态"),
    ]
    docs = []
    regimes = []
    for i in range(10000):
        dom, reg = domains[i % len(domains)]
        t_title, t_desc = topics[i % len(topics)]
        docs.append(
            {
                "title": f"{t_title}（样本规则 {i}）",
                "sub_domain": t_title[:6],
                "subject_theme": "财务与余额" if i % 2 == 0 else "风控与合规管控",
                "capability_name": dom,
                "regime": reg,
                "business_condition": f"{t_desc}；适用编号批次 {i % 97}。",
                "business_action": "纳入当期监管合规报送统计汇总。",
            }
        )
        regimes.append(reg)

    fw = {
        "title": 2.5,
        "sub_domain": 1.8,
        "subject_theme": 1.7,
        "capability_name": 1.6,
        "regime": 1.5,
        "business_condition": 1.2,
        "business_action": 1.0,
    }
    fields = tuple(fw.keys())
    idx = CachedBM25Index(docs, fields=fields, regimes=regimes)

    queries = [
        "1104 G01 质押存单扣减净额",
        "SAFE 外汇休眠销户剔除",
        "跨渠道重复流水去重",
        "完全不存在的航天发射指标",
    ]
    for q in queries:
        t0 = time.monotonic()
        cached_scores = idx.score(q, field_weights=fw)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        assert elapsed_ms < 100.0, f"10k BM25 query too slow: {elapsed_ms:.1f}ms for {q}"

        if q == queries[0]:
            # 与全量原生 bm25_score_documents 做前 500 采样校验一致性 + 前缀子集精确比对
            sub_docs = docs[:500]
            sub_regs = regimes[:500]
            sub_idx = CachedBM25Index(sub_docs, fields=fields, regimes=sub_regs)
            ref = bm25_score_documents(q, sub_docs, field_weights=fw, regimes=sub_regs)
            got = sub_idx.score(q, field_weights=fw)
            assert len(ref) == len(got)
            for a, b in zip(ref, got):
                assert abs(a - b) < 1e-6


def test_bm25_cache_invalidation_on_catalog_mutation(tmp_path):
    invalidate_bm25_caches()
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    load_builtin_sample_pack(session, settings=settings)

    rules = list_business_rules(session)
    sig1 = get_catalog_cache_signature(session, "rule")
    docs = [{"title": r["title"], "business_condition": r["business_condition"]} for r in rules]
    idx1 = get_or_build_bm25_index((sig1, "test"), docs, fields=("title", "business_condition"))
    idx1_again = get_or_build_bm25_index((sig1, "test"), docs, fields=("title", "business_condition"))
    assert idx1 is idx1_again

    # 人工确认规则会修改 updated_at 并触发 invalidate_bm25_caches
    target = rules[0]
    confirm_business_rule(session, target["id"], expected_version=1, confirmer="合规复核人")
    sig2 = get_catalog_cache_signature(session, "rule")
    idx2 = get_or_build_bm25_index((sig2, "test"), docs, fields=("title", "business_condition"))
    assert idx2 is not idx1
