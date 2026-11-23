"""通用 sqlglot AST 解析泛化、宏观文档分层过滤、纯业务血缘及 LLM 配置接口测试。"""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.common import db_session
from app.catalog.synthesizer import ingest_material, load_real_sac_corpus, synthesize_business_assets
from app.catalog.views import get_asset_panorama
from app.copilot.answer_engine import ask_business_copilot
from app.infra.config import Settings
from app.infra.db import Base
from app.infra.db_tables import BusinessRuleAssetModel, LogicEvidenceModel
from app.ingestion.ast_extractor import extract_sql_ast_skeleton
from app.ingestion.doc_parser import parse_regulatory_document
from app.main import create_app
from app.policy.dc_bus import detect_technical_jargon

from .helpers import make_session


def test_sqlglot_ast_generalizes_to_unfamiliar_wealth_sql_without_hardcoding(tmp_path):
    """面对完全陌生的理财赎回清算 SQL（无任何 SAC 字段或关键字），仅靠 sqlglot AST 自动还原纯业务解释。"""
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))

    unfamiliar_sql = """
    /* 程序描述: 净值型理财产品到期赎回清分 */
    WITH eligible_orders AS (
        SELECT o.order_id, o.investor_id, o.redeem_units * p.nav_price AS redeem_amt,
               ROW_NUMBER() OVER (PARTITION BY o.investor_id, o.order_id ORDER BY o.confirm_ts DESC) AS rn
        FROM wm_redeem_orders o
        INNER JOIN wm_product_nav p ON o.product_code = p.product_code
        WHERE o.trade_dt BETWEEN '2026-01-01' AND '2026-01-31'
          AND o.audit_status <> 'REJECTED'
          AND NOT EXISTS (SELECT 1 FROM wm_frozen_investors f WHERE f.investor_id = o.investor_id)
    )
    INSERT INTO wm_settlement_ledger (investor_id, settle_total, order_cnt)
    SELECT e.investor_id,
           SUM(CASE WHEN e.redeem_amt >= 10000 THEN e.redeem_amt ELSE 0 END) AS settle_total,
           COUNT(1) AS order_cnt
    FROM eligible_orders e
    WHERE e.rn = 1
    GROUP BY e.investor_id;
    """.encode("utf-8")

    skeleton = extract_sql_ast_skeleton(unfamiliar_sql.decode("utf-8"))
    assert skeleton["ast_parsed"] is True
    assert "WM_SETTLEMENT_LEDGER" in skeleton["target_tables"]
    assert "WM_REDEEM_ORDERS" in skeleton["source_tables"]
    assert skeleton["has_date_window"] is True
    assert "NOT_EXISTS" in skeleton["exclusion_kinds"]
    assert "NEQ" in skeleton["exclusion_kinds"]
    assert skeleton["has_window_dedup"] is True
    assert "SUM" in skeleton["aggregate_funcs"]

    ingest_material(
        session,
        title="wealth_redemption_clear.sql",
        material_kind="CODE_LOGIC",
        file_type="sql",
        content=unfamiliar_sql,
        settings=settings,
    )
    session.commit()
    synthesize_business_assets(session, settings=settings)

    pano = get_asset_panorama(session)
    caps = pano["capabilities"]
    assert len(caps) == 1
    assert caps[0]["name"] == "净值型理财产品到期赎回清分"
    assert caps[0]["coverage_status"] == "COVERED"
    assert caps[0]["rule_count"] >= 4

    serialized = json.dumps(
        {
            "capabilities": pano["capabilities"],
            "rules": pano["rules"],
            "calibers": pano["calibers"],
            "reconcile": pano["reconcile"],
        },
        ensure_ascii=False,
    )
    assert detect_technical_jargon(serialized) == []
    for raw_ident in ("wm_redeem_orders", "WM_SETTLEMENT_LEDGER", "investor_id", "ROW_NUMBER", "NOT EXISTS"):
        assert raw_ident not in serialized

    ans = ask_business_copilot(session, "净值型理财产品到期赎回清分目前的逻辑包含了哪些规则？", settings=settings)
    assert ans["verification"]["de_jargon_passed"] is True
    assert "理财" in ans["answer"]


def test_controlled_llm_ast_translation_rejects_jargon_and_retries(tmp_path):
    """配置 LLM 时，如果模型首次翻译泄露表名，Bus 零黑话门禁拦截并回喂模型修正后入库。"""
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))

    class MockTranslatorLlm:
        def __init__(self):
            self.calls = 0
            self.feedbacks = []

        def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
            self.calls += 1
            self.feedbacks.append(feedback)
            if tool_name == "translate_logic_evidence" and self.calls == 1:
                return {
                    "business_domain_hint": "消费分期提前还款核销",
                    "target_concept_hint": "分期提前结清",
                    "source_summary": "从 TBL_LOAN_REPAY WHERE DEL_FLAG <> 'Y' 计算利息。",
                    "calculation_summary": "SUM(INTEREST_AMT) 求和。",
                }, {"provider": "mock", "model": "mock"}
            return {
                "business_domain_hint": "消费分期提前还款核销",
                "target_concept_hint": "分期提前结清核销",
                "source_summary": "当持卡人申请提前结清消费分期时，核验当期有效分期本金与应付利息并过滤作废撤销申请。",
                "calculation_summary": "按分期账户汇总应收剩余本金与分段利息，冲正记录不计入。",
                "inclusions": [
                    {
                        "rule_code": "INC_EARLY_PAYOFF",
                        "title": "提前结清分期申请纳入核销",
                        "condition": "持卡人在账单日区间内提交有效提前还款确认",
                        "rationale": "及时结清分期余额并终止后续息费计提。",
                    }
                ],
                "exclusions": [],
                "branches": [],
                "dedup_grain_hint": "同一分期合约仅保留最新一条核销生效记录。",
            }, {"provider": "mock", "model": "mock"}

    mock_llm = MockTranslatorLlm()
    ingest_material(
        session,
        title="installment_payoff.sql",
        material_kind="CODE_LOGIC",
        file_type="sql",
        content="INSERT INTO LEDGER_X SELECT acct_no, SUM(fee) FROM BILL_REPAY WHERE status <> 'VOID' GROUP BY acct_no;".encode("utf-8"),
        settings=settings,
        llm=mock_llm,
    )
    session.commit()

    assert mock_llm.calls == 2
    assert mock_llm.feedbacks[0] is None
    assert "去技术化门禁拦截" in (mock_llm.feedbacks[1] or "")

    ev = session.query(LogicEvidenceModel).first()
    assert ev is not None
    assert ev.business_domain_hint == "消费分期提前还款核销"
    assert detect_technical_jargon(ev.source_summary) == []


def test_macro_policy_doc_filtering_and_cross_business_lineage(tmp_path):
    """宏观指导文件应过滤非数据类段落（如组织架构/绿色数据中心），且业务全景能给出无表名纯业务上下游流向。"""
    doc_bytes = """# 银行业保险业数字金融高质量发展实施方案
（一）加强人才与组织保障
5.建设复合型人才队伍与乡村振兴服务网点。
7.绿色数据中心与东数西算绿电建设。
（六）高效推动监管数字化智能化转型
31.深入推进监管大数据建设。加快推进监管报表“一表通”建设，实现监管数据资源统一标准、统一采集、统一管理。
""".encode("utf-8")
    clauses = parse_regulatory_document("一表通_数字金融高质量发展实施方案_测试.md", doc_bytes)
    assert len(clauses) == 1
    assert "一表通" in clauses[0].business_requirement
    assert clauses[0].source_locator["doc_tier"] == "MACRO_POLICY"

    sql_dir = Path("/Users/hang/code/sql_explorer")
    if (sql_dir / "sample_scripts").exists():
        session = make_session()
        load_real_sac_corpus(
            session,
            sql_explorer_dir=str(sql_dir),
            settings=Settings(blob_dir=str(tmp_path / "blobs")),
        )
        pano = get_asset_panorama(session)
        caps = {c["name"]: c for c in pano["capabilities"]}
        # 一表通不再被宏观文档拆出 38 条非数据管理条款
        assert caps["一表通互联网贷款与信贷资产转让报送"]["rule_count"] <= 10
        # 验证至少识别出跨业务板块的纯业务上下游流向且不含任何物理表名
        has_lineage = any(c["upstream_businesses"] or c["downstream_businesses"] for c in pano["capabilities"])
        assert has_lineage is True
        lineage_text = json.dumps(
            [(c["upstream_businesses"], c["downstream_businesses"]) for c in pano["capabilities"]],
            ensure_ascii=False,
        )
        assert detect_technical_jargon(lineage_text) == []


def test_llm_config_endpoint_masks_api_key_and_preserves_secret(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr("app.api.platform.system.get_settings", lambda: Settings(blob_dir=str(tmp_path / "blobs")))
    app = create_app()
    app.dependency_overrides[db_session] = _override_db

    with TestClient(app) as client:
        r0 = client.get("/api/settings/llm-config")
        assert r0.status_code == 200
        assert r0.json()["config"]["llm_provider"] == "scripted"
        assert r0.json()["config"]["has_api_key"] is False

        r1 = client.put(
            "/api/settings/llm-config",
            json={
                "llm_provider": "openai_compatible",
                "llm_base_url": "https://example.invalid/v1",
                "llm_model": "gpt-4o-mini",
                "llm_api_key": "sk-secret-token-value",
                "llm_timeout": 90,
            },
        )
        assert r1.status_code == 200
        cfg1 = r1.json()["config"]
        assert cfg1["llm_provider"] == "openai_compatible"
        assert cfg1["has_api_key"] is True
        assert cfg1["llm_api_key"] == ""  # 读取接口绝不回显明文密钥

        # 仅改 timeout 不传新密钥时应保留原密钥
        r2 = client.put(
            "/api/settings/llm-config",
            json={
                "llm_provider": "openai_compatible",
                "llm_base_url": "https://example.invalid/v1",
                "llm_model": "gpt-4o-mini",
                "llm_api_key": "",
                "llm_timeout": 150,
            },
        )
        assert r2.status_code == 200
        assert r2.json()["config"]["has_api_key"] is True
        assert r2.json()["config"]["llm_timeout"] == 150
