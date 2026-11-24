"""纯业务 Copilot 工具、强制披露闸门与 Bus 去技术化拦截测试。"""

import json

from app.catalog.synthesizer import load_builtin_sample_pack
from app.copilot.answer_engine import ask_business_copilot
from app.copilot.business_tools import BUSINESS_TOOL_SCHEMAS, execute_business_tool
from app.copilot.disclosure import DISCLOSURE_TEXT
from app.infra.config import Settings
from app.infra.llm import call_llm_validated
from app.main import create_app
from app.policy.dc_bus import default_bus, detect_technical_jargon
from fastapi.testclient import TestClient

from .helpers import make_session, make_wal_db


def test_all_seven_business_tools_return_pure_business_language(tmp_path):
    session = make_session()
    load_builtin_sample_pack(session, settings=Settings(blob_dir=str(tmp_path / "blobs")))

    assert len(BUSINESS_TOOL_SCHEMAS) == 7

    res_cov = execute_business_tool(session, "list_covered_businesses", {})
    assert len(res_cov["covered_businesses"]) == 3
    assert len(res_cov["unrealized_businesses"]) == 1
    assert res_cov["disclose"] is True
    assert res_cov["disclose_kind"] == "unrealized"

    res_scope = execute_business_tool(session, "get_business_scope_and_rules", {"topic": "应报未报 剔除"})
    assert len(res_scope["rules"]) >= 1
    assert detect_technical_jargon(json.dumps(res_scope, ensure_ascii=False)) == []

    res_cal = execute_business_tool(session, "get_business_caliber_detail", {"metric": "联名账户 余额"})
    assert res_cal["disclose"] is True
    assert res_cal["disclose_kind"] == "divergent"
    assert detect_technical_jargon(json.dumps(res_cal, ensure_ascii=False)) == []

    res_gap = execute_business_tool(session, "check_regulatory_coverage_gap", {})
    assert len(res_gap["divergent_items"]) >= 1
    assert len(res_gap["unrealized_items"]) >= 1

    res_role = execute_business_tool(session, "find_rules_by_business_role", {"role": "合规"})
    assert len(res_role["rules"]) >= 1

    res_concept = execute_business_tool(session, "resolve_business_concept", {"concept": "个人存款分户账"})
    assert len(res_concept["capabilities"]) >= 1

    code = res_scope["rules"][0]["code"]
    res_back = execute_business_tool(session, "verify_implementation_backing", {"asset_codes": [code]})
    assert res_back["verdicts"][0]["logic_backing_count"] >= 1
    assert isinstance(res_back["verdicts"][0].get("processing_steps"), list)
    assert detect_technical_jargon(json.dumps(res_back, ensure_ascii=False)) == []


def test_copilot_qa_covers_scope_exclusions_calibers_and_mandatory_disclosures(tmp_path):
    session = make_session()
    load_builtin_sample_pack(session, settings=Settings(blob_dir=str(tmp_path / "blobs")))

    # 1. 问“包含哪些业务”：能如实列举已包含业务，且同时披露尚未包含的制度要求
    ans1 = ask_business_copilot(session, "我们的逻辑里面已经包含了哪些业务？")
    assert ans1["verification"]["de_jargon_passed"] is True
    assert "大额与可疑外汇交易报送" in ans1["answer"]
    assert "个人存款分户账明细加工" in ans1["answer"]
    assert "应报未报漏报风险识别" in ans1["answer"]
    assert DISCLOSURE_TEXT["unrealized"] in ans1["answer"]

    # 2. 问“应报未报为什么被剔除”：讲业务风险隔离含义，不提 NOT EXISTS / DFVLOC / 表名
    ans2 = ask_business_copilot(session, "为什么应报未报账户会被自动剔除？")
    assert ans2["verification"]["de_jargon_passed"] is True
    assert "待补报风险账户" in ans2["answer"] or "应报未报" in ans2["answer"]
    assert "NOT EXISTS" not in ans2["answer"]
    assert "DFVLOC" not in ans2["answer"]
    assert "SAC_" not in ans2["answer"]

    # 3. 问“联名账户余额怎么算”：强制披露多口径分歧状态
    ans3 = ask_business_copilot(session, "交易后余额和跨境联名账户折算余额目前分别按什么口径算？")
    assert ans3["verification"]["de_jargon_passed"] is True
    assert DISCLOSURE_TEXT["divergent"] in ans3["answer"]
    assert "静态" in ans3["answer"]

    # 4. 问完全无关的问题：诚实告知未覆盖（not_found 披露），绝不胡编
    ans4 = ask_business_copilot(session, "期货期权保证金日间强平规则是什么？")
    assert ans4["verification"]["de_jargon_passed"] is True
    assert DISCLOSURE_TEXT["not_found"] in ans4["answer"]


def test_bus_de_jargon_and_citation_adjudicators_intercept_llm_hallucinations(tmp_path):
    session = make_session()
    load_builtin_sample_pack(session, settings=Settings(blob_dir=str(tmp_path / "blobs")))

    class LeakingThenCleanLlm:
        def __init__(self):
            self.calls = 0
            self.feedbacks = []

        def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
            self.calls += 1
            self.feedbacks.append(feedback)
            if self.calls == 1:
                # 首次违规泄露表名与 SQL 关键词
                return {
                    "answer": "通过查询 SF_SAC_EXCHANGE_DETAILS_INFO WHERE NOT EXISTS 实现剔除。",
                    "cited_asset_codes": payload["allowed_asset_codes"][:1],
                }, {"provider": "test", "model": "test"}
            if self.calls == 2:
                # 第二次违规编造未检索到的资产编号
                return {
                    "answer": "已纳入大额与可疑外汇交易申报主体。",
                    "cited_asset_codes": ["rule-fake-hallucinated"],
                }, {"provider": "test", "model": "test"}
            return {
                "answer": "系统会纳入当期申报区间内在重点监控客户清单里的交易，并把缺少报送属地映射的账户单独列入漏报风险清单。",
                "cited_asset_codes": payload["allowed_asset_codes"][:1],
            }, {"provider": "test", "model": "test"}

    # 用 call_llm_validated 直接验证 Bus 拦截与回喂修正闭环
    bus = default_bus()
    fake_llm = LeakingThenCleanLlm()
    out, trace = call_llm_validated(
        fake_llm,
        "compose_business_answer",
        "回答业务问题",
        {"allowed_asset_codes": ["cap-test-01"]},
        backoff=(0, 0),
        bus=bus,
        validation_attempts=4,
    )
    assert fake_llm.calls == 3
    assert "去技术化门禁拦截" in fake_llm.feedbacks[1]
    assert "业务资产引用越界" in fake_llm.feedbacks[2]
    assert len(trace["bus_rejections"]) == 2
    assert detect_technical_jargon(out["answer"]) == []

    # 验证通过 ask_business_copilot 主链驱动也能自动收敛
    res = ask_business_copilot(session, "我们的逻辑里面已经包含了哪些业务？", llm=LeakingThenCleanLlm())
    assert res["verification"]["de_jargon_passed"] is True


def test_copilot_stream_endpoint_emits_steps_and_result(tmp_path, monkeypatch):
    engine, session_factory = make_wal_db(tmp_path, "stream")
    with session_factory() as s:
        load_builtin_sample_pack(s, settings=Settings(blob_dir=str(tmp_path / "blobs")))
        s.commit()

    monkeypatch.setattr("app.api.copilot.chat.SessionLocal", session_factory)
    monkeypatch.setattr("app.infra.config.get_settings", lambda: Settings(blob_dir=str(tmp_path / "blobs")))

    app = create_app()
    events = []
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/copilot/ask/stream",
            json={"query": "我们的逻辑里面已经包含了哪些业务？", "role_lens": "业务"},
        ) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))

    engine.dispose()
    step_events = [e for e in events if e.get("type") == "step"]
    meta_events = [e for e in events if e.get("type") == "meta"]
    delta_events = [e for e in events if e.get("type") == "answer_delta"]
    result_events = [e for e in events if e.get("type") == "result"]
    assert any(e.get("id") == "plan" and e.get("status") == "done" for e in step_events)
    assert any(e.get("id") == "compose" and e.get("status") == "done" for e in step_events)
    assert len(meta_events) == 1
    assert len(delta_events) > 1
    streamed_body = "".join(e.get("delta", "") for e in delta_events)
    assert "大额与可疑外汇交易报送" in streamed_body
    assert len(result_events) == 1
    assert "大额与可疑外汇交易报送" in result_events[0]["data"]["answer"]
