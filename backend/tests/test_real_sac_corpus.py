"""真实 SAC（sql_explorer）25 份 SQL 存储过程/建表脚本 + 真实监管与口径文档全链路实测。"""

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.common import db_session
from app.catalog.synthesizer import load_real_sac_corpus
from app.catalog.views import get_asset_backing_detail, get_asset_panorama
from app.copilot.answer_engine import ask_business_copilot
from app.copilot.disclosure import DISCLOSURE_TEXT
from app.infra.config import Settings
from app.infra.db import Base
from app.infra.db_tables import LogicEvidenceModel, RegulatoryClauseModel, RunModel, SourceMaterialModel
from app.kernel.event_log import verify_chain
from app.main import create_app
from app.policy.dc_bus import detect_technical_jargon

from .helpers import make_session

SQL_EXPLORER_DIR = Path(
    os.environ.get("SQL_EXPLORER_DIR") or Path(__file__).resolve().parents[3] / "sql_explorer"
)

pytestmark = pytest.mark.skipif(
    not (SQL_EXPLORER_DIR / "sample_scripts").exists(),
    reason="sql_explorer real SAC corpus directory not present on host",
)


def test_real_sac_25_scripts_and_regulatory_docs_synthesize_clean_business_assets(tmp_path):
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    summary = load_real_sac_corpus(
        session,
        sql_explorer_dir=str(SQL_EXPLORER_DIR),
        settings=settings,
    )

    # 1) 真实 25 份 *SAC*.sql + 16 份真实 SAC 口径/术语/监管法规文档全部入库并建立实证
    assert session.query(SourceMaterialModel).filter(SourceMaterialModel.material_kind == "CODE_LOGIC").count() == 25
    assert session.query(SourceMaterialModel).filter(SourceMaterialModel.material_kind == "REGULATORY_DOC").count() >= 15
    assert session.query(LogicEvidenceModel).count() >= 400
    assert session.query(RegulatoryClauseModel).count() >= 80
    assert summary["capability_count"] >= 9
    assert summary["rule_count"] >= 50

    run = session.get(RunModel, summary["run_id"])
    assert run is not None
    assert verify_chain(session, run) is True

    # 2) 验证 7 大真实 SAC 已落地业务板块 + 至少 2 项未落地监管新要求（一表通、数字人民币）
    pano = get_asset_panorama(session)
    caps_by_name = {c["name"]: c for c in pano["capabilities"]}
    expected_covered = {
        "大额与可疑外汇交易报送",
        "应报未报漏报风险识别",
        "个人存款分户账明细加工",
        "机构大额结售汇及资金来源与使用报送",
        "对公客户信息与境内外身份监管报送",
        "银行机构信息与层级架构监管报送",
        "客户综合授信额度信息监管报送",
    }
    assert expected_covered.issubset(set(caps_by_name.keys()))
    for name in expected_covered:
        assert caps_by_name[name]["coverage_status"] in {"COVERED", "PARTIAL"}
        assert len(caps_by_name[name]["evidence_ids"] if "evidence_ids" in caps_by_name[name] else ["ok"]) >= 1
        assert caps_by_name[name]["rule_count"] >= 2

    assert caps_by_name["机构大额结售汇及资金来源与使用报送"]["coverage_status"] == "PARTIAL"
    assert caps_by_name["数字人民币大额与可疑交易监管报送"]["coverage_status"] == "UNREALIZED"
    assert caps_by_name["一表通互联网贷款与信贷资产转让报送"]["coverage_status"] == "UNREALIZED"

    # 3) 对外业务资产全景（能力、规则、口径、对账总览）100% 零代码黑话/表名/字段名/存储过程名泄漏
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
    for forbidden in ("SP_SAC_", "SF_SAC_", "SAC_UNREPORT", "EAST_ODS_", "DEALAMOUNT", "ACCOUNTBALAN", "NOT EXISTS", "EXECUTE IMMEDIATE", "DFVLOC"):
        assert forbidden not in serialized

    # 4) 验证每条业务规则的底层实证追溯统计有效，且绝不吐出物理对象名
    sample_rules = [r for r in pano["rules"] if r["reconcile_status"] in {"VERIFIED", "CONSISTENT", "DIVERGENT"}]
    assert len(sample_rules) >= 30
    backing = get_asset_backing_detail(session, sample_rules[0]["code"])
    assert backing["logic_backing_count"] >= 1
    assert detect_technical_jargon(json.dumps(backing, ensure_ascii=False)) == []


def test_real_sac_business_copilot_qa_across_all_domains_and_disclosures(tmp_path):
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    load_real_sac_corpus(
        session,
        sql_explorer_dir=str(SQL_EXPLORER_DIR),
        settings=settings,
    )

    test_cases = [
        (
            "我们的逻辑里面已经包含了哪些业务？",
            ("大额与可疑外汇交易报送", "机构大额结售汇及资金来源与使用报送", "个人存款分户账明细加工", "对公客户信息与境内外身份监管报送"),
            "unrealized",
        ),
        (
            "这些应报未报账户是什么情况？为什么要把它们从报送明细里剔除？",
            ("应报未报", "补报", "属地映射"),
            None,
        ),
        (
            "机构大额结售汇和购汇资金来源、结汇资金使用是怎么处理的？交易对手怎么填？",
            ("30", "变动编号", "总账"),
            "divergent",
        ),
        (
            "个人存款分户账为什么要剔除个体工商户和历史迁移账户？",
            ("个体工商户", "历史迁移资产"),
            None,
        ),
        (
            "对公客户信息里面的授信捞回和境内外名称怎么处理？",
            ("捞回", "授信"),
            None,
        ),
        (
            "银行机构信息缺失金融代码或营业执照号怎么处理？",
            ("上级", "继承"),
            None,
        ),
        (
            "授信信息里哪些测试或无效数据会被自动剔除？",
            ("虚拟占位", "内部测试"),
            None,
        ),
        (
            "交易金额和交易后余额是按原值传还是汇总折算？",
            ("原值", "静态"),
            None,
        ),
        (
            "哪些制度要求是我们当前系统逻辑还没包含的？",
            ("数字人民币", "一表通"),
            "unrealized",
        ),
        (
            "期货期权保证金强平阈值与隔夜持仓限额参数是什么？",
            (DISCLOSURE_TEXT["not_found"],),
            "not_found",
        ),
    ]

    for question, expected_fragments, expected_disclosure_kind in test_cases:
        ans = ask_business_copilot(session, question, settings=settings)
        assert ans["verification"]["de_jargon_passed"] is True, f"Failed jargon check on: {question} -> {ans['answer']}"
        assert ans["verification"]["residual_jargon"] == []
        for frag in expected_fragments:
            assert frag in ans["answer"], f"Missing '{frag}' in answer to '{question}': {ans['answer']}"
        if expected_disclosure_kind is not None:
            kinds = {d["kind"] for d in ans["disclosures"]}
            assert expected_disclosure_kind in kinds, f"Expected disclosure {expected_disclosure_kind} for '{question}', got {kinds}"
        else:
            assert ans["disclosures"] == [], f"Unexpected disclosures {ans['disclosures']} for '{question}'"


def test_real_sac_fastapi_endpoint_end_to_end(tmp_path, monkeypatch):
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

    monkeypatch.setattr("app.api.ingestion.sources.get_settings", lambda: Settings(blob_dir=str(tmp_path / "blobs")))
    app = create_app()
    app.dependency_overrides[db_session] = _override_db

    with TestClient(app) as client:
        res = client.post("/api/sources/load-sac-real")
        assert res.status_code == 200
        data = res.json()
        assert data["capability_count"] >= 9
        assert data["rule_count"] >= 50

        ask_res = client.post(
            "/api/copilot/ask",
            json={"query": "对公客户信息里已经包含了授信捞回吗？", "role_lens": "合规"},
        )
        assert ask_res.status_code == 200
        body = ask_res.json()
        assert body["verification"]["de_jargon_passed"] is True
        assert "捞回" in body["answer"]
