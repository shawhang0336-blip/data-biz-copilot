"""dc_agent 多线程并发与高压竞态回归测试（-W error::sqlalchemy.exc.SAWarning）。"""

from concurrent.futures import ThreadPoolExecutor
import threading

from app.catalog.mutations import confirm_business_rule, update_business_rule
from app.catalog.synthesizer import ingest_material, load_builtin_sample_pack
from app.catalog.views import get_asset_panorama
from app.copilot.answer_engine import ask_business_copilot, get_copilot_session_detail
from app.infra.config import Settings
from app.infra.db_tables import EventModel, LogicEvidenceModel, RunModel, SourceMaterialModel
from app.kernel.event_log import append_event, verify_chain
from app.kernel.run import create_run as kernel_create_run

from .helpers import make_wal_db


def _run_concurrent(tasks):
    gate = threading.Barrier(len(tasks))
    results = [None] * len(tasks)
    errors = [None] * len(tasks)

    def _worker(idx, fn):
        gate.wait(timeout=10)
        try:
            results[idx] = fn()
        except Exception as exc:  # noqa: BLE001
            errors[idx] = exc

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futs = [pool.submit(_worker, idx, fn) for idx, fn in enumerate(tasks)]
        for fut in futs:
            fut.result(timeout=30)
    return results, errors


def test_stress_concurrent_material_ingestion_cas_and_copilot_session(tmp_path):
    engine, Session = make_wal_db(tmp_path, "dc_stress")
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    try:
        # 1) 10 线程并发导入完全相同的逻辑脚本：零 IntegrityError，底层实证仅落 1 份
        same_sql = "/* 业务说明: 并发幂等测试 */ INSERT INTO TMP_TEST SELECT * FROM BDM_SRC WHERE VALID_FLAG='1';".encode("utf-8")

        def _ingest():
            with Session() as s:
                row = ingest_material(
                    s,
                    title="same_logic.sql",
                    material_kind="CODE_LOGIC",
                    file_type="sql",
                    content=same_sql,
                    settings=settings,
                )
                s.commit()
                return row.id

        ids, ingest_errs = _run_concurrent([_ingest for _ in range(10)])
        assert [e for e in ingest_errs if e is not None] == []
        assert len(set(ids)) == 1

        # 2) 载入完整样例包后，8 线程并发 CAS 改写同版本号规则：严格 1 成功、7 VERSION_CONFLICT
        with Session() as seed:
            load_builtin_sample_pack(seed, settings=settings)
            rule_id = get_asset_panorama(seed)["rules"][0]["id"]
            assert seed.query(SourceMaterialModel).filter(SourceMaterialModel.title == "same_logic.sql").count() == 1
            assert seed.query(LogicEvidenceModel).count() >= 5

        def _cas_edit(idx):
            with Session() as s:
                row = update_business_rule(
                    s,
                    rule_id,
                    expected_version=1,
                    changes={"business_action": f"业务口径并发修订版本 {idx}"},
                    editor=f"user_{idx}",
                    reason="cas stress",
                )
                return row.current_version

        edit_res, edit_errs = _run_concurrent([lambda idx=i: _cas_edit(idx) for i in range(8)])
        assert sum(1 for r in edit_res if r == 2) == 1
        assert sum(1 for e in edit_errs if isinstance(e, ValueError) and "VERSION_CONFLICT" in str(e)) == 7

        # 8 线程并发签字确认 expected_version=2：严格 1 成功（v3 CONFIRMED），7 冲突
        def _cas_confirm(idx):
            with Session() as s:
                row = confirm_business_rule(s, rule_id, expected_version=2, confirmer=f"ba_{idx}")
                return row.current_version

        conf_res, conf_errs = _run_concurrent([lambda idx=i: _cas_confirm(idx) for i in range(8)])
        assert sum(1 for r in conf_res if r == 3) == 1
        assert sum(1 for e in conf_errs if isinstance(e, ValueError) and "VERSION_CONFLICT" in str(e)) == 7

        # 3) 12 线程并发追加 EventLog + 8 线程同 session_id 并发向 Copilot 提问
        with Session() as run_seed:
            run = kernel_create_run(run_seed, "stress_run")
            run_seed.commit()
            run_id = run.id

        def _append(worker_idx):
            for step in range(4):
                with Session() as s:
                    r = s.get(RunModel, run_id)
                    append_event(s, r, "SYNTHESIS_STEP", f"w_{worker_idx}", {"step": step})
                    s.commit()

        _, ev_errs = _run_concurrent([lambda idx=i: _append(idx) for i in range(12)])
        assert [e for e in ev_errs if e is not None] == []

        shared_sid = "stress-shared-copilot-session"

        def _ask(idx):
            with Session() as s:
                return ask_business_copilot(
                    s,
                    f"第{idx}次询问：我们的逻辑里面已经包含了哪些业务？",
                    session_id=shared_sid,
                    settings=settings,
                )

        ask_res, ask_errs = _run_concurrent([lambda idx=i: _ask(idx) for i in range(8)])
        assert [e for e in ask_errs if e is not None] == []
        assert all(r["verification"]["de_jargon_passed"] for r in ask_res)

        with Session() as verify:
            run_obj = verify.get(RunModel, run_id)
            events = verify.query(EventModel).filter(EventModel.run_id == run_id).order_by(EventModel.seq.asc()).all()
            assert len(events) == 1 + 48
            assert len({e.prev_hash for e in events}) == len(events)
            assert verify_chain(verify, run_obj) is True

            detail = get_copilot_session_detail(verify, shared_sid)
            assert len(detail["messages"]) == 16
    finally:
        engine.dispose()
