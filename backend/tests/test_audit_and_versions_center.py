"""Data Biz Copilot 审计中心、业务变更动态与 Rule Studio 跨产品交接接口回归测试。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.common import db_session as api_db_session
from app.catalog.mutations import confirm_business_rule, update_business_rule
from app.infra.db import Base
from app.infra.db_tables import (
    BusinessCaliberAssetModel,
    BusinessCapabilityModel,
    BusinessRuleAssetModel,
    EventModel,
    IngestionJobModel,
    RegulatoryClauseModel,
    RunModel,
    SourceMaterialModel,
)
from app.infra.retrieval import agency_for_regime, matches_agency_or_regime, rule_studio_regulator_code
from app.kernel.event_log import append_event
from app.main import app


def _make_threadsafe_sf():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _make_client(sf):
    def _override():
        db = sf()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[api_db_session] = _override
    return TestClient(app)


def test_banking_agency_taxonomy_mapping():
    assert agency_for_regime("EAST") == "NFRA"
    assert agency_for_regime("一表通") == "NFRA"
    assert agency_for_regime("1104") == "NFRA"
    assert agency_for_regime("人行统计") == "PBOC"
    assert agency_for_regime("SAFE") == "SAFE"
    assert matches_agency_or_regime("EAST", agency="NFRA") is True
    assert matches_agency_or_regime("SAFE", agency="NFRA") is False
    assert rule_studio_regulator_code("一表通") == "YBT"
    assert rule_studio_regulator_code("SAFE") == "SAFE_BIZ"


def test_audit_overview_detail_and_tamper_detection():
    sf = _make_threadsafe_sf()
    session = sf()
    try:
        run_id = str(uuid.uuid4())
        run = RunModel(id=run_id, spec_id="dc_batch_ingest")
        job = IngestionJobModel(
            id=str(uuid.uuid4()),
            run_id=run_id,
            name="EAST 5.0 工程批量解析",
            source_mode="ZIP_ARCHIVE",
            status="COMPLETED",
            total_files=12,
            tier1_count=4,
            tier2_count=2,
            tier3_count=6,
            template_clone_count=3,
            completed_count=6,
            llm_calls_used=9,
            tokens_used=8400,
            elapsed_seconds=18,
        )
        session.add_all([run, job])
        session.flush()

        append_event(session, run, "RUN_CREATED", "created", {"spec_id": "dc_batch_ingest"})
        append_event(
            session,
            run,
            "BUS_REJECTED",
            "tier1_wave",
            {"tool": "translate_logic_evidence", "reason": "de_jargon 拦截物理表名 TMP_LOAN"},
        )
        append_event(
            session,
            run,
            "BUS_REJECTED",
            "auto_synthesize",
            {"tool": "synthesize_domain_assets", "reason": "evidence_backing 实证白名单越界"},
        )
        append_event(session, run, "BATCH_COMPLETED", "finalize_batch", {"completed": 6})
        session.commit()

        client = _make_client(sf)

        ov = client.get("/api/runs/audit-overview")
        assert ov.status_code == 200
        body = ov.json()
        assert body["summary"]["total_runs"] == 1
        assert body["summary"]["chain_valid_runs"] == 1
        assert body["summary"]["chain_broken_runs"] == 0
        assert body["summary"]["total_bus_rejections"] == 2
        assert body["summary"]["de_jargon_rejections"] == 1
        assert body["summary"]["evidence_rejections"] == 1
        assert body["summary"]["total_llm_calls"] == 9

        det = client.get(f"/api/runs/{run_id}/audit-detail")
        assert det.status_code == 200
        detail = det.json()
        assert detail["chain_valid"] is True
        assert len(detail["events"]) == 4
        guard_types = {r["guard_type"] for r in detail["bus_rejections"]}
        assert guard_types == {"DE_JARGON", "EVIDENCE_BACKING"}
        assert detail["fingerprints"]["job_summary"]["total_files"] == 12

        # 模拟直接篡改数据库中的某条事件，实时验链必须立刻报错并指出断链 seq
        ev = (
            session.query(EventModel)
            .filter(EventModel.run_id == run_id, EventModel.kind == "BATCH_COMPLETED")
            .first()
        )
        ev.payload_json = {"completed": 999}
        session.commit()

        ov_broken = client.get("/api/runs/audit-overview").json()
        assert ov_broken["summary"]["chain_broken_runs"] == 1
        det_broken = client.get(f"/api/runs/{run_id}/audit-detail").json()
        assert det_broken["chain_valid"] is False
        assert det_broken["broken_seq"] == ev.seq
    finally:
        app.dependency_overrides.pop(api_db_session, None)
        session.close()


def test_asset_revisions_and_rule_studio_handoff_export():
    sf = _make_threadsafe_sf()
    session = sf()
    try:
        mat = SourceMaterialModel(
            id=str(uuid.uuid4()),
            title="EAST5_个人存款规范.pdf",
            material_kind="REGULATORY_DOC",
            file_type="pdf",
            sha256="a" * 64,
            blob_uri="blob://east5",
            status="PARSED",
        )
        clause = RegulatoryClauseModel(
            id=str(uuid.uuid4()),
            material_id=mat.id,
            chapter="第三章",
            business_domain="个人存款分户账明细加工",
            clause_title="销户账户不得纳入余额汇总",
            clause_kind="EXCLUSION",
            business_requirement="已销户分户账余额不得计入期末存款汇总",
            source_locator_json={"regime": "EAST"},
        )
        cap = BusinessCapabilityModel(
            id=str(uuid.uuid4()),
            code="CAP_EAST_DEP",
            name="个人存款分户账明细加工",
            description="EAST 个人存款报送",
            coverage_status="PARTIAL",
            clause_ids_json=[clause.id],
        )
        rule = BusinessRuleAssetModel(
            id=str(uuid.uuid4()),
            capability_id=cap.id,
            code="RULE_EAST_01",
            title="剔除已销户个人分户账",
            rule_category="EXCLUSION",
            business_condition="账户已办理正式销户",
            business_action="自动剔除不计入期末余额",
            business_rationale="遵循 EAST 存款统计口径",
            reconcile_status="UNREALIZED",
            clause_ids_json=[clause.id],
            status="DRAFT",
            current_version=1,
        )
        cal = BusinessCaliberAssetModel(
            id=str(uuid.uuid4()),
            capability_id=cap.id,
            code="CAL_EAST_01",
            metric_name="个人存款期末余额",
            formula_summary="按日终有效分户账余额汇总",
            reconcile_status="DIVERGENT",
            conflict_notes="历史分支对睡眠户处理存在差异",
            clause_ids_json=[clause.id],
            status="DRAFT",
            current_version=1,
        )
        session.add_all([mat, clause, cap, rule, cal])
        session.commit()

        # 人工修订 + 业务确认
        update_business_rule(
            session,
            rule.id,
            expected_version=1,
            changes={"business_action": "自动剔除并登记差异说明"},
            editor="李复核",
            reason="明确业务处置动作",
        )
        confirm_business_rule(
            session,
            rule.id,
            expected_version=2,
            confirmer="王处长",
            comment="确认无误",
        )

        client = _make_client(sf)

        rev_resp = client.get("/api/assets/revisions", params={"agency": "NFRA"})
        assert rev_resp.status_code == 200
        rev_body = rev_resp.json()
        assert rev_body["summary"]["total"] == 2
        assert rev_body["summary"]["wording_edits"] == 1
        assert rev_body["summary"]["confirmations"] == 1
        assert rev_body["revisions"][0]["regime"] == "EAST"
        assert rev_body["revisions"][0]["agency"] == "NFRA"

        # 导出 Rule Studio 交接包（包含 UNREALIZED + DIVERGENT）
        handoff_resp = client.get("/api/assets/rule-studio-export", params={"agency": "NFRA"})
        assert handoff_resp.status_code == 200
        handoff = handoff_resp.json()
        assert handoff["schema"] == "data-agent.rule-studio-handoff.v1"
        assert handoff["total_items"] == 2
        assert {it["rule_studio_regulator"] for it in handoff["items"]} == {"EAST"}
        assert {it["reconcile_status"] for it in handoff["items"]} == {"UNREALIZED", "DIVERGENT"}
        assert len(handoff["items"][0]["regulatory_clauses"]) == 1
    finally:
        app.dependency_overrides.pop(api_db_session, None)
        session.close()
