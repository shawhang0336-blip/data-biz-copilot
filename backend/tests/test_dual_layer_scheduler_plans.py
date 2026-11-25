"""双层 Scheduler（内层纯内存 + 外层 DB 账本）与四套静态 WorkflowPlan / Channel 契约验证测试。"""

import json

import pytest
from sqlalchemy import event as sa_event

from app.api.platform.system import get_synthesis_run
from app.catalog.batch_ingest import create_batch_ingestion_job, start_batch_job
from app.infra.config import Settings
from app.infra.db_tables import RunModel
from app.ingestion.code_parser import parse_code_logic
from app.ingestion.doc_parser import parse_regulatory_document
from app.kernel.event_log import GENESIS_HASH, verify_chain
from app.kernel.plan import PlanEdge, PlanNode, WorkflowPlan
from app.kernel.run import AmbiguousWriteError, ChannelSpec, add_number, create_memory_context, last_value, merge_dict
from app.kernel.scheduler import Outcome, Scheduler, Send
from app.policy import (
    CODE_PARSE_CHANNELS,
    DOC_PARSE_CHANNELS,
    BATCH_INGEST_CHANNELS,
    SYNTHESIS_CHANNELS,
    _apply,
    detect_technical_jargon,
)

from .helpers import make_wal_db


class _DualLayerScriptedLlm:
    """确定性脚本化假 LLM：覆盖内层代码/制度解析与外层领域提纯，零真实网络请求。"""

    def __init__(self):
        self.calls = []

    def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
        self.calls.append(tool_name)
        if tool_name == "plan_code_structure":
            steps = payload.get("steps") or [{"step_index": 0}, {"step_index": 1}]
            mid = max(1, len(steps) // 2)
            first = [s["step_index"] for s in steps[:mid]]
            second = [s["step_index"] for s in steps[mid:]] or [first[0]]
            return (
                {
                    "business_overview": "个人大额跨境汇款数据筛选与汇总加工流水线",
                    "canonical_domain": "个人大额跨境汇款报送",
                    "chunk_groups": [
                        {
                            "stage_title": "交易范围筛选与风险剔除",
                            "stage_role": "FILTER",
                            "stage_purpose": "保留个人客户达标交易并剔除测试客户与撤单记录",
                            "business_domain": "个人大额跨境汇款报送",
                            "step_indices": first,
                        },
                        {
                            "stage_title": "按申报主体归并去重输出",
                            "stage_role": "AGGREGATE",
                            "stage_purpose": "按申报客户维度汇总折算金额并保持唯一记录输出",
                            "business_domain": "个人大额跨境汇款报送",
                            "step_indices": second,
                        },
                    ],
                },
                {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
            )
        if tool_name == "translate_logic_evidence":
            stage_meta = (payload.get("code_plan") or {}).get("stage_title") or "交易加工"
            return (
                {
                    "business_domain_hint": "个人大额跨境汇款报送",
                    "target_concept_hint": "大额跨境汇款业务台账",
                    "source_summary": f"{stage_meta}：按个人主体筛选有效跨境汇款并剔除测试户与冲正流水。",
                    "inclusions": [
                        {
                            "rule_code": "R_INC_1",
                            "title": "有效个人客户交易",
                            "condition": "仅纳入状态正常且达到报送标准的个人跨境汇款记录",
                            "rationale": "确保个人大额跨境汇款申报统计口径完整准确",
                        }
                    ],
                    "exclusions": [
                        {
                            "rule_code": "R_EXC_1",
                            "title": "剔除测试与冲正交易",
                            "condition": "测试体验客户与当日已冲正流水不进入报送范围",
                            "rationale": "防止测试与失效冲正流水污染监管申报数据",
                        }
                    ],
                    "branches": [],
                    "calculation_kind": "AGGREGATE",
                    "calculation_summary": "按客户汇总有效交易折算金额，避免重复申报。",
                    "dedup_grain_hint": "同一客户同一申报期仅保留唯一 1 条记录",
                },
                {"prompt_tokens": 15, "completion_tokens": 12, "total_tokens": 27},
            )
        if tool_name == "plan_document_structure":
            sections = payload.get("sections") or []
            return (
                {
                    "document_kind": "REGULATORY_SPEC",
                    "document_summary": "个人大额跨境汇款监管报送制度规范",
                    "chunking_strategy": "DOMAIN_SECTIONS",
                    "canonical_domains": ["个人大额跨境汇款报送"],
                    "section_assignments": [
                        {
                            "section_index": s["section_index"],
                            "role": "ACTIONABLE_RULE",
                            "business_domain": "个人大额跨境汇款报送",
                            "chunk_group": 1 if idx < max(1, len(sections) // 2) else 2,
                        }
                        for idx, s in enumerate(sections)
                    ],
                },
                {"prompt_tokens": 18, "completion_tokens": 12, "total_tokens": 30},
            )
        if tool_name == "extract_regulatory_clauses":
            sections = payload.get("sections") or []
            return (
                {
                    "clauses": [
                        {
                            "section_index": s["section_index"],
                            "keep": True,
                            "business_domain": "个人大额跨境汇款报送",
                            "clause_title": s["title"],
                            "clause_kind": s.get("draft_kind") or "RULE",
                            "business_requirement": f"{s['title']}：按纯中文监管口径执行大额跨境汇款报送校验。",
                            "audience": "业务 / 监管报送",
                            "metrics_mentioned": ["折算金额"],
                        }
                        for s in sections
                    ]
                },
                {"prompt_tokens": 16, "completion_tokens": 12, "total_tokens": 28},
            )
        if tool_name in {"refine_business_rules", "refine_business_calibers", "synthesize_domain_assets"}:
            return (
                {"capability_description": "个人大额跨境汇款业务合规报送", "rules": [], "calibers": []},
                {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            )
        raise AssertionError(f"Unexpected tool call: {tool_name}")


def test_in_memory_scheduler_fanout_channels_and_ambiguous_write_guard():
    plan = WorkflowPlan(
        name="mem_fanout_check",
        entry="created",
        nodes=(
            PlanNode("created", "初始化", "stage"),
            PlanNode("start", "启动分片", "stage"),
            PlanNode("worker", "并发工作片", "stage"),
            PlanNode("merge", "归并收敛", "terminal"),
        ),
        edges=(
            PlanEdge("created", "start", "always"),
            PlanEdge("start", "worker", "fanout"),
            PlanEdge("worker", "merge", "always"),
        ),
    )
    channels = {
        "results": ChannelSpec("results", merge_dict, allow_multi=True),
        "count": ChannelSpec("count", add_number, allow_multi=True),
        "single_slot": ChannelSpec("single_slot", last_value),
    }
    ctx = create_memory_context("mem_fanout_check", channels=channels, reducer=_apply)
    assert ctx.is_in_memory is True
    assert ctx.session is None

    def _start(_inp, _ctx):
        return Outcome(
            when="fanout",
            sends=[Send("worker", {"idx": 0}, label="w0"), Send("worker", {"idx": 1}, label="w1")],
        )

    def _worker(inp, _ctx):
        idx = inp["idx"]
        return Outcome(
            shared={"results": {str(idx): f"ok-{idx}"}, "count": 1},
            output=idx,
            events=[("WORK_DONE", "worker", {"idx": idx, "label": f"w{idx}"})],
        )

    def _merge(items, local_ctx):
        return Outcome(
            output={"items": items, "count": local_ctx.get("count")},
            events=[("BATCH_COMPLETED", "merge", {"count": local_ctx.get("count")})],
        )

    sched = Scheduler(plan, {"start": _start, "worker": _worker, "merge": _merge})
    sched.drive(ctx, max_workers=2)

    assert ctx.get("results") == {"0": "ok-0", "1": "ok-1"}
    assert ctx.get("count") == 2
    assert ctx.state.status == "COMPLETED"
    assert len(ctx.memory_events) >= 4
    assert ctx.memory_events[0].prev_hash == GENESIS_HASH
    for i in range(1, len(ctx.memory_events)):
        assert ctx.memory_events[i].prev_hash == ctx.memory_events[i - 1].event_hash

    # 同波次多个并发任务写同一个 last_value 单写者通道必须在屏障被 AmbiguousWriteError 拦截
    bad_ctx = create_memory_context("mem_fanout_conflict", channels=channels, reducer=_apply)

    def _bad_worker(inp, _ctx):
        return Outcome(shared={"single_slot": inp["idx"]})

    bad_sched = Scheduler(plan, {"start": _start, "worker": _bad_worker, "merge": _merge})
    with pytest.raises(AmbiguousWriteError):
        bad_sched.drive(bad_ctx, max_workers=2)


def test_inner_memory_schedulers_for_code_and_doc_parsers():
    assert CODE_PARSE_CHANNELS["stage_results"].allows_multi_writer is True
    assert CODE_PARSE_CHANNELS["code_plan"].allows_multi_writer is False
    assert DOC_PARSE_CHANNELS["batch_results"].allows_multi_writer is True
    assert BATCH_INGEST_CHANNELS["phase_stats"].allows_multi_writer is True
    assert SYNTHESIS_CHANNELS["refined_domains"].allows_multi_writer is True

    llm = _DualLayerScriptedLlm()
    from app.policy.dc_bus import default_bus

    bus = default_bus()
    sql_bytes = """
    -- 程序说明：个人大额跨境汇款筛查与申报汇总
    INSERT INTO TMP_FX_STAGE1
    SELECT CUST_NO, TXN_AMT FROM ODS_FX_TXN WHERE TEST_FLAG <> 'Y' AND TXN_AMT >= 50000;

    INSERT INTO RPT_FX_REPORT
    SELECT CUST_NO, SUM(TXN_AMT) FROM TMP_FX_STAGE1 GROUP BY CUST_NO;
    """.encode("utf-8")

    evidences = parse_code_logic("P_FX_CROSS_BORDER.sql", sql_bytes, llm=llm, bus=bus, execution_mode="full")
    assert len(evidences) == 2
    assert "plan_code_structure" in llm.calls
    assert "translate_logic_evidence" in llm.calls
    for ev in evidences:
        assert (ev.internal_locator or {}).get("plan_stage") == "PLAN_THEN_EXTRACT"
        assert not detect_technical_jargon(ev.source_summary), ev.source_summary

    doc_md = """# 个人大额跨境汇款报送规范
## 第一条 纳入标准与门槛要求
个人单笔或当日累计等值五万美元以上跨境汇款需按规定开展合规报送。
## 第二条 剔除规则与测试交易过滤
测试验证客户及当日已撤销冲正流水不得纳入最终监管申报清单。
""".encode("utf-8")

    clauses = parse_regulatory_document("个人大额跨境汇款规范.md", doc_md, llm=llm, bus=bus)
    assert len(clauses) == 2
    assert "plan_document_structure" in llm.calls
    assert "extract_regulatory_clauses" in llm.calls
    for cl in clauses:
        assert "个人大额跨境汇款报送" in cl.business_domain
        assert (cl.source_locator or {}).get("ai_refined") is True
        assert not detect_technical_jargon(cl.business_requirement)


def test_outer_batch_ingest_and_synthesis_schedulers_with_run_spans_api(tmp_path):
    engine, Session = make_wal_db(tmp_path, "dual_layer_outer")
    settings = Settings(blob_dir=str(tmp_path / "blobs"), llm_provider="scripted", llm_api_key="")
    llm = _DualLayerScriptedLlm()

    try:
        files = [
            {
                "title": "个人大额跨境汇款规范.md",
                "relative_path": "docs/个人大额跨境汇款规范.md",
                "material_kind": "REGULATORY_DOC",
                "file_type": "md",
                "content": """# 个人大额跨境汇款报送规范
## 第一条 纳入标准与门槛要求
个人单笔或当日累计等值五万美元以上跨境汇款需按规定开展合规报送。
""".encode("utf-8"),
            },
            {
                "title": "rpt_fx_report.sql",
                "relative_path": "rpt/rpt_fx_report.sql",
                "material_kind": "CODE_LOGIC",
                "file_type": "sql",
                "content": """-- 个人大额跨境汇款申报主逻辑
INSERT INTO TMP_FX_STEP
SELECT CUST_NO, TXN_AMT FROM ODS_FX_TXN WHERE TXN_AMT >= 50000;
INSERT INTO RPT_FX_REPORT
SELECT CUST_NO, SUM(TXN_AMT) FROM TMP_FX_STEP GROUP BY CUST_NO;
""".encode("utf-8"),
            },
        ]

        with Session() as s:
            detail = create_batch_ingestion_job(
                s,
                files,
                job_name="双层调度闭环测试",
                settings=settings,
                auto_synthesize=True,
            )
            assert detail["job"]["status"] == "READY_REVIEW"
            batch_run_id = detail["job"]["run_id"]

            parked_obs = get_synthesis_run(batch_run_id, session=s)
            assert parked_obs["status"] == "WAITING_REVIEW"
            assert parked_obs["current_node"] == "tier_review_gate"
            assert parked_obs["chain_valid"] is True
            assert [e["kind"] for e in parked_obs["events"]] == [
                "RUN_CREATED",
                "BATCH_SCANNED",
                "TIER_REVIEW_REQUIRED",
            ]

            finished = start_batch_job(s, detail["job"]["id"], settings=settings, llm=llm, inline=True)
            assert finished["status"] == "COMPLETED"
            assert finished["completed_count"] == 2

            completed_obs = get_synthesis_run(batch_run_id, session=s)
            assert completed_obs["spec_id"] == "dc_batch_ingest"
            assert completed_obs["status"] == "COMPLETED"
            assert completed_obs["chain_valid"] is True
            assert completed_obs["spans"], "Batch run should expose Scheduler Plan spans"
            batch_nodes = {sp["node"] for sp in completed_obs["spans"]}
            assert {"static_scan", "finalize_batch"} <= batch_nodes

            syn_runs = s.query(RunModel).filter(RunModel.spec_id == "dc_asset_synthesis").all()
            assert len(syn_runs) == 1
            syn_obs = get_synthesis_run(syn_runs[0].id, session=s)
            assert syn_obs["spec_id"] == "dc_asset_synthesis"
            assert syn_obs["status"] == "COMPLETED"
            assert syn_obs["chain_valid"] is True
            syn_kinds = [e["kind"] for e in syn_obs["events"]]
            assert "SOURCES_PARSED" in syn_kinds
            assert "EVIDENCE_ALIGNED" in syn_kinds
            assert "ASSETS_SYNTHESIZED" in syn_kinds
            assert syn_obs["spans"], "Asset synthesis run should expose Scheduler Plan spans"
            assert verify_chain(s, syn_runs[0]) is True
    finally:
        engine.dispose()
