"""千级批量接入断点续跑、单脚本预算隔离、ZIP 安全解压与 API 端到端测试。"""

import io
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.common import db_session
from app.catalog.batch_ingest import (
    create_batch_ingestion_job,
    extract_zip_archive_safely,
    get_batch_job_detail,
    override_material_tier,
    recover_stale_batch_jobs,
    resume_batch_job,
    start_batch_job,
)
from app.catalog.synthesizer import synthesize_business_assets
from app.catalog.views import get_asset_panorama
from app.infra.config import Settings
from app.infra.db import Base
from app.infra.db_tables import IngestionJobModel, LogicEvidenceModel, SourceMaterialModel, SystemSettingModel
from app.main import create_app
from app.policy.dc_bus import detect_technical_jargon

from .helpers import make_wal_db


def _build_zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, text in entries:
            zf.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


class _CheckpointStubLlm:
    def __init__(self, *, fail_keywords=()):
        self.fail_keywords = tuple(fail_keywords)
        self.call_log = []

    def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
        import json

        blob = json.dumps(payload, ensure_ascii=False)
        self.call_log.append((tool_name, blob[:120]))
        for kw in self.fail_keywords:
            if kw in blob:
                raise RuntimeError(f"Simulated transient failure on {kw}")
        if tool_name == "plan_code_structure":
            steps = payload.get("steps") or [{"step_index": 0}]
            return (
                {
                    "business_overview": "监管业务数据加工与汇总输出",
                    "canonical_domain": "信贷业务监管报送",
                    "chunk_groups": [
                        {
                            "stage_title": "主体筛选与汇总阶段",
                            "business_domain": "信贷业务监管报送",
                            "stage_role": "CALCULATION",
                            "step_indices": [s["step_index"] for s in steps],
                            "stage_purpose": "执行纳入筛选与口径加工",
                        }
                    ],
                },
                {"usage": {"total_tokens": 40}},
            )
        if tool_name == "synthesize_domain_assets":
            rules = payload.get("draft_rules") or []
            return (
                {
                    "description": f"{payload.get('domain_name')}核心监管加工逻辑。",
                    "scope_summary": "覆盖正常客户贷款业务筛选与净额计算。",
                    "rules": [{"code": r["code"]} for r in rules],
                    "calibers": [],
                },
                {"usage": {"total_tokens": 60}},
            )
        return (
            {
                "business_domain_hint": payload.get("business_domain_hint") or "信贷业务监管报送",
                "target_concept_hint": "对公有效贷款净额",
                "source_summary": "筛选正常存续对公贷款并扣减测试及核销记录。",
                "calculation_kind": "AGGREGATE",
                "calculation_summary": "汇总扣减后的对公贷款有效余额。",
                "inclusions": [
                    {
                        "rule_code": "INC_ACTIVE_LOAN",
                        "title": "纳入正常存续对公贷款",
                        "condition": "贷款状态正常且非测试账户",
                        "rationale": "符合当期监管报送范围",
                    }
                ],
                "exclusions": [],
                "branches": [],
                "dedup_grain_hint": "按客户汇总唯一结果。",
            },
            {"usage": {"total_tokens": 50}},
        )


def test_batch_checkpoint_pause_and_zero_duplicate_resume(tmp_path):
    engine, Session = make_wal_db(tmp_path, "ckpt_resume")
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    try:
        files = [
            {"title": "ods_src.sql", "relative_path": "ods/ods_src.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": b"INSERT INTO ODS_LOAN SELECT CUST_NO, BAL FROM SRC_LOAN;"},
            {"title": "dwd_clean.sql", "relative_path": "dwd/dwd_clean.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": "/* 上游贷款清洗 */ INSERT INTO DWD_LOAN_CLEAN SELECT CUST_NO, BAL FROM ODS_LOAN WHERE TEST_FLG <> 'Y';".encode("utf-8")},
            {"title": "rpt_1104_g01_br01.sql", "relative_path": "rpt/rpt_1104_g01_br01.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": "/* 1104 G01 分行01 */ INSERT INTO RPT_1104_G01_BR01 SELECT CUST_NO, SUM(BAL) FROM DWD_LOAN_CLEAN GROUP BY CUST_NO;".encode("utf-8")},
            {"title": "rpt_1104_g01_br02.sql", "relative_path": "rpt/rpt_1104_g01_br02.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": "/* 1104 G01 分行02 */ INSERT INTO RPT_1104_G01_BR02 SELECT CUST_NO, SUM(BAL) FROM DWD_LOAN_CLEAN GROUP BY CUST_NO;".encode("utf-8")},
            {"title": "rpt_1104_g04.sql", "relative_path": "rpt/rpt_1104_g04.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": "/* 1104 G04 汇总 */ INSERT INTO RPT_1104_G04 SELECT CUST_NO, SUM(BAL) FROM DWD_LOAN_CLEAN WHERE BAL > 100 GROUP BY CUST_NO;".encode("utf-8")},
            {"title": "old_bak.sql", "relative_path": "bak/old_bak.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": b"INSERT INTO RPT_OLD SELECT * FROM DWD_LOAN_CLEAN;"},
        ]

        with Session() as s:
            # 预算故意设小为 max_llm_calls=2，保证 Tier 2 完成且 Tier 1 只完成 1 份后触发 PAUSED_BUDGET
            s.add(
                SystemSettingModel(
                    key="execution_budget",
                    value_json={
                        "max_retries": 2,
                        "max_llm_concurrency": 1,
                        "max_llm_rpm": 0,
                        "max_calls_per_script": 4,
                        "max_llm_calls": 2,
                        "max_total_tokens": 5000000,
                        "max_elapsed_seconds": 3600,
                    },
                )
            )
            s.commit()

            detail = create_batch_ingestion_job(s, files, job_name="千脚本断点测试", settings=settings)
            job_id = detail["job"]["id"]
            assert detail["job"]["tier1_count"] == 2
            assert detail["job"]["tier2_count"] == 1
            assert detail["job"]["tier3_count"] == 3
            assert detail["job"]["template_clone_count"] == 1

            llm = _CheckpointStubLlm()
            paused = start_batch_job(s, job_id, settings=settings, llm=llm, inline=True)
            assert paused["status"] == "PAUSED_BUDGET"
            assert paused["completed_count"] == 2
            assert len(llm.call_log) == 2

            # 模拟强杀重启遗留 1 个 PARSING 状态脚本
            stuck = (
                s.query(SourceMaterialModel)
                .filter(SourceMaterialModel.batch_job_id == job_id, SourceMaterialModel.status == "STATIC_INDEXED", SourceMaterialModel.tier == "TIER_1_CORE")
                .first()
            )
            assert stuck is not None
            stuck.status = "PARSING"
            s.commit()

            rec = recover_stale_batch_jobs(s)
            assert rec["reset_materials"] == 1

            # 调高预算后断点续跑：应只调剩下的那 1 份 Tier 1（1 次调用），绝不重跑已完成的 2 份
            budget_row = s.get(SystemSettingModel, "execution_budget")
            budget_row.value_json = {**budget_row.value_json, "max_llm_calls": 20}
            s.commit()

            llm.call_log.clear()
            done = resume_batch_job(s, job_id, settings=settings, llm=llm, inline=True)
            assert done["status"] == "COMPLETED"
            assert done["completed_count"] == 3
            assert done["skipped_count"] == 3
            assert done["failed_count"] == 0
            assert len(llm.call_log) == 1

            # 分行克隆脚本应零成本继承 Master 实证，且被吸收的 Tier 2 默认不独立成域
            evs = s.query(LogicEvidenceModel).all()
            assert any((e.internal_locator_json or {}).get("is_template_clone") is True for e in evs)
            syn = synthesize_business_assets(s, settings=settings)
            assert syn["capability_count"] >= 1
            pano = get_asset_panorama(s)
            assert "上游贷款清洗" not in {c["name"] for c in pano["capabilities"]}
            assert detect_technical_jargon(str(pano["rules"])) == []
    finally:
        engine.dispose()


def test_per_script_budget_breach_isolates_single_bad_script(tmp_path):
    engine, Session = make_wal_db(tmp_path, "per_script_guard")
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    try:
        # script_big 有 4 步（会超过 max_calls_per_script=1），script_ok 只有 1 步（只需 1 次）
        big_sql = "\n".join(
            f"/* 步骤{i} */ INSERT INTO RPT_BIG_{i} SELECT CUST_NO, BAL FROM DWD_SRC WHERE AMT > {i};"
            for i in range(4)
        ).encode("utf-8")
        ok_sql = "/* 正常单步报送 */ INSERT INTO RPT_OK_G01 SELECT CUST_NO, SUM(BAL) FROM DWD_SRC WHERE STATUS='1' GROUP BY CUST_NO;".encode("utf-8")

        files = [
            {"title": "rpt_big.sql", "relative_path": "rpt/rpt_big.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": big_sql},
            {"title": "rpt_ok.sql", "relative_path": "rpt/rpt_ok.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": ok_sql},
        ]
        with Session() as s:
            s.add(
                SystemSettingModel(
                    key="execution_budget",
                    value_json={
                        "max_retries": 2,
                        "max_llm_concurrency": 1,
                        "max_llm_rpm": 0,
                        "max_calls_per_script": 1,
                        "max_llm_calls": 50,
                        "max_total_tokens": 5000000,
                        "max_elapsed_seconds": 3600,
                    },
                )
            )
            s.commit()
            detail = create_batch_ingestion_job(s, files, job_name="单脚本隔离测试", settings=settings)
            job_id = detail["job"]["id"]
            res = start_batch_job(s, job_id, settings=settings, llm=_CheckpointStubLlm(), inline=True)
            assert res["status"] == "COMPLETED"
            assert res["completed_count"] == 1
            assert res["failed_count"] == 1

            failed_detail = get_batch_job_detail(s, job_id, status="FAILED")
            assert len(failed_detail["materials"]) == 1
            assert failed_detail["materials"][0]["title"] == "rpt_big.sql"
    finally:
        engine.dispose()


def test_zip_slip_blocked_and_batch_api_endpoints_e2e(tmp_path, monkeypatch):
    bad_zip = _build_zip_bytes([("../../evil.sql", "SELECT 1;")])
    try:
        extract_zip_archive_safely(bad_zip)
        raise AssertionError("Expected ValueError on Zip Slip path")
    except ValueError as exc:
        assert "路径穿越" in str(exc)

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

    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    monkeypatch.setattr("app.infra.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.ingestion.sources.get_settings", lambda: settings)

    # 创建本地工程目录用于 import-dir 测试
    proj_dir = tmp_path / "sample_repo"
    (proj_dir / "rpt").mkdir(parents=True)
    (proj_dir / "bak").mkdir(parents=True)
    (proj_dir / "rpt" / "rpt_g01.sql").write_text(
        "/* 1104 G01 贷款台账 */ INSERT INTO RPT_G01 SELECT CUST_NO, SUM(BAL) FROM ODS_A WHERE FLAG='1' GROUP BY CUST_NO;",
        encoding="utf-8",
    )
    (proj_dir / "bak" / "rpt_g01_bak.sql").write_text("INSERT INTO RPT_G01_BAK SELECT * FROM ODS_A;", encoding="utf-8")

    app = create_app()
    app.dependency_overrides[db_session] = _override_db

    good_zip = _build_zip_bytes(
        [
            ("ods/ods_a.sql", "INSERT INTO ODS_A SELECT CUST_NO, BAL FROM SRC_X;"),
            ("rpt/rpt_a.sql", "/* 报送终表 */ INSERT INTO RPT_A SELECT CUST_NO, SUM(BAL) FROM ODS_A WHERE BAL > 0 GROUP BY CUST_NO;"),
            ("docs/rule.md", "# 信贷监管报送\n## 纳入范围\n正常存续客户纳入报送。"),
        ]
    )

    with TestClient(app) as client:
        up_res = client.post(
            "/api/sources/batch-upload",
            files={"files": ("repo.zip", good_zip, "application/zip")},
            data={"material_kind": "AUTO", "job_name": "ZIP测试工程", "auto_start": "false"},
        )
        assert up_res.status_code == 201
        job_data = up_res.json()
        job_id = job_data["job"]["id"]
        assert job_data["job"]["status"] == "READY_REVIEW"
        assert job_data["job"]["total_files"] == 3

        # 测试人工调级 PATCH 接口
        ods_item = next(m for m in job_data["materials"] if m["title"] == "ods_a.sql")
        patch_res = client.patch(
            f"/api/sources/jobs/{job_id}/materials/{ods_item['id']}/tier",
            json={"tier": "TIER_1_CORE"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["tier"] == "TIER_1_CORE"

        jobs_list = client.get("/api/sources/jobs")
        assert jobs_list.status_code == 200
        assert any(j["id"] == job_id for j in jobs_list.json())

        # 测试 import-dir 端点
        dir_res = client.post(
            "/api/sources/import-dir",
            json={"dir_path": str(proj_dir), "auto_start": False},
        )
        assert dir_res.status_code == 201
        dir_job = dir_res.json()["job"]
        assert dir_job["total_files"] == 2
        assert dir_job["tier3_count"] == 1

        # 测试旧 /api/sources/synthesize 空包体与新增量包体双兼容
        assert client.post("/api/sources/synthesize").status_code == 200
        assert client.post("/api/sources/synthesize", json={"target_domains": []}).status_code == 200


def test_http_402_billing_error_pauses_batch_without_marking_scripts_failed(tmp_path):
    engine, Session = make_wal_db(tmp_path, "billing_pause")
    settings = Settings(blob_dir=str(tmp_path / "blobs"), llm_provider="scripted", llm_api_key="")
    try:
        files = [
            {"title": "dwd_step.sql", "relative_path": "dwd/dwd_step.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": b"/* DWD */ INSERT INTO DWD_X SELECT CUST_NO FROM ODS_X WHERE BAL > 0;"},
            {"title": "rpt_g01.sql", "relative_path": "rpt/rpt_g01.sql", "material_kind": "CODE_LOGIC", "file_type": "sql", "content": b"/* 1104 G01 */ INSERT INTO RPT_G01 SELECT CUST_NO, SUM(1) FROM DWD_X GROUP BY CUST_NO;"},
        ]

        class _BillingStopLlm:
            def __init__(self, fail_now: bool):
                self.fail_now = fail_now

            def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
                if self.fail_now:
                    raise RuntimeError("LLM HTTP 402 invalid_request_error: Insufficient Balance")
                return _CheckpointStubLlm().assign(tool_name, instruction, payload, feedback, timeout)

        with Session() as s:
            s.add(
                SystemSettingModel(
                    key="execution_budget",
                    value_json={
                        "max_retries": 2,
                        "max_llm_concurrency": 1,
                        "max_llm_rpm": 0,
                        "max_calls_per_script": 6,
                        "max_llm_calls": 50,
                        "max_total_tokens": 5000000,
                        "max_elapsed_seconds": 3600,
                    },
                )
            )
            s.commit()
            det = create_batch_ingestion_job(s, files, job_name="余额暂停测试", settings=settings)
            jid = det["job"]["id"]
            paused = start_batch_job(s, jid, settings=settings, llm=_BillingStopLlm(fail_now=True), inline=True)
            assert paused["status"] == "PAUSED_BUDGET"
            assert paused["failed_count"] == 0
            assert "余额" in paused["error_summary"]

            resumed = resume_batch_job(s, jid, settings=settings, llm=_BillingStopLlm(fail_now=False), inline=True)
            assert resumed["status"] == "COMPLETED"
            assert resumed["completed_count"] == 2
            assert resumed["failed_count"] == 0
    finally:
        engine.dispose()
