"""双源静默解析、业务资产合成、去技术化清洗与 API 端到端测试。"""

import io
import json

from docx import Document
from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.common import db_session
from app.catalog.mutations import confirm_business_rule, update_business_rule
from app.catalog.synthesizer import ingest_material, load_builtin_sample_pack, synthesize_business_assets
from app.catalog.views import get_asset_backing_detail, get_asset_panorama
from app.infra.config import Settings
from app.infra.db import Base
from app.infra.db_tables import LogicEvidenceModel, RunModel
from app.kernel.event_log import verify_chain
from app.main import create_app
from app.policy.dc_bus import detect_technical_jargon

from .helpers import make_session


def _docx_bytes() -> bytes:
    doc = Document()
    doc.add_heading("跨境人民币专项报送", level=1)
    doc.add_heading("纳入范围说明", level=2)
    doc.add_paragraph("企业跨境人民币收支需按申报周期纳入专项统计报送。")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "业务口径表"
    ws.append(["业务规则", "口径要求"])
    ws.append(["休眠户过滤", "剔除连续三年无动账的长期休眠账户"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_builtin_sample_pack_fuses_code_and_docs_with_zero_jargon_leak(tmp_path):
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    summary = load_builtin_sample_pack(session, settings=settings)

    assert summary["capability_count"] == 4
    assert summary["rule_count"] >= 7
    assert summary["caliber_count"] >= 4

    run = session.get(RunModel, summary["run_id"])
    assert run is not None
    assert verify_chain(session, run) is True

    # 底层实证表内部保留了物理定位线索，但仅作内部审计依据
    evs = session.query(LogicEvidenceModel).all()
    assert len(evs) >= 4
    assert any(ev.internal_locator_json.get("physical_objects") for ev in evs)

    # 对外业务全景视图绝不能包含任何 SQL 关键字、物理表名或字段英文名
    panorama = get_asset_panorama(session)
    serialized = json.dumps(
        {
            "capabilities": panorama["capabilities"],
            "rules": panorama["rules"],
            "calibers": panorama["calibers"],
            "reconcile": panorama["reconcile"],
        },
        ensure_ascii=False,
    )
    assert detect_technical_jargon(serialized) == []

    # 校验四种对账状态齐全：已一致落地、存在口径分歧、系统尚未包含
    statuses = {c["reconcile_status"] for c in panorama["calibers"]}
    assert {"VERIFIED", "DIVERGENT", "UNREALIZED"}.issubset(statuses)

    # 校验业务依据明细接口只返回条款+实证数，零技术泄漏
    sample_rule = panorama["rules"][0]
    backing = get_asset_backing_detail(session, sample_rule["code"])
    assert backing["logic_backing_count"] >= 0
    assert detect_technical_jargon(json.dumps(backing, ensure_ascii=False)) == []


def test_docx_xlsx_and_custom_sql_parsing_synthesis(tmp_path):
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    ingest_material(session, title="跨境制度.docx", material_kind="REGULATORY_DOC", file_type="docx", content=_docx_bytes(), settings=settings)
    ingest_material(session, title="口径字典.xlsx", material_kind="REGULATORY_DOC", file_type="xlsx", content=_xlsx_bytes(), settings=settings)
    ingest_material(
        session,
        title="custom_logic.sql",
        material_kind="CODE_LOGIC",
        file_type="sql",
        content="/* 业务说明: 个人存款分户账明细清洗 */ INSERT INTO TMP_X SELECT * FROM BDM_EVT WHERE VALID_FLAG='1' AND CANCEL_FLAG <> 'Y';".encode("utf-8"),
        settings=settings,
    )
    session.commit()
    res = synthesize_business_assets(session, settings=settings)
    assert res["capability_count"] >= 2
    panorama = get_asset_panorama(session)
    assert detect_technical_jargon(json.dumps(panorama["rules"], ensure_ascii=False)) == []


def test_asset_cas_mutations_and_jargon_block(tmp_path):
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    load_builtin_sample_pack(session, settings=settings)
    rule = get_asset_panorama(session)["rules"][0]

    with pytest.raises(ValueError, match="BUSINESS_LANGUAGE_ONLY"):
        update_business_rule(
            session,
            rule["id"],
            expected_version=1,
            changes={"business_condition": "WHERE TMP_SAC_TBL.CUST_NO IS NOT NULL"},
            editor="测试员",
        )

    updated = update_business_rule(
        session,
        rule["id"],
        expected_version=1,
        changes={"business_action": "纳入当期重点监测报送清单，并生成业务台账留存"},
        editor="业务专员",
        reason="统一措辞",
    )
    assert updated.current_version == 2

    with pytest.raises(ValueError, match="VERSION_CONFLICT"):
        confirm_business_rule(session, rule["id"], expected_version=1, confirmer="负责人")

    confirmed = confirm_business_rule(session, rule["id"], expected_version=2, confirmer="负责人")
    assert confirmed.status == "CONFIRMED"
    assert confirmed.current_version == 3


def test_fastapi_endpoints_end_to_end(tmp_path, monkeypatch):
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

    monkeypatch.setattr("app.infra.config.get_settings", lambda: Settings(blob_dir=str(tmp_path / "blobs")))
    monkeypatch.setattr("app.api.ingestion.sources.get_settings", lambda: Settings(blob_dir=str(tmp_path / "blobs")))
    app = create_app()
    app.dependency_overrides[db_session] = _override_db

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        load_res = client.post("/api/sources/load-sample")
        assert load_res.status_code == 200
        assert load_res.json()["capability_count"] == 4

        pano = client.get("/api/assets/panorama").json()
        assert len(pano["capabilities"]) == 4

        ask_res = client.post(
            "/api/copilot/ask",
            json={"query": "我们的逻辑里面已经包含了哪些业务？", "role_lens": "业务"},
        )
        assert ask_res.status_code == 200
        body = ask_res.json()
        assert body["verification"]["de_jargon_passed"] is True
        assert "大额与可疑外汇交易报送" in body["answer"]
