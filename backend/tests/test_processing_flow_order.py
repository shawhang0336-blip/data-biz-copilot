"""加工步骤按真实执行顺序分组、去技术化且不泄露脚本名/块序号的回归测试。"""

import json

from app.catalog.views import summarize_processing_flow
from app.infra.db_tables import LogicEvidenceModel
from app.policy.dc_bus import detect_technical_jargon

from .helpers import make_session


def _ev(eid, script, chunk, title, role, summary, **extra):
    return LogicEvidenceModel(
        id=eid,
        material_id=f"mat-{script}",
        logic_fingerprint=eid,
        business_domain_hint="机构大额结售汇资金来源与使用",
        target_concept_hint=title or "机构大额结售汇及资金来源与使用报送数据底座结构",
        source_summary=summary,
        inclusion_predicates_json=extra.get("inc", []),
        exclusion_predicates_json=extra.get("exc", []),
        branch_cases_json=extra.get("br", []),
        calculation_kind="DIRECT",
        calculation_summary=extra.get("calc", "按既定业务规则加工流转，无复杂指标口径改写。"),
        dedup_grain_hint=extra.get("dedup", ""),
        internal_locator_json={
            "script": script,
            "chunk_index": chunk,
            "start_line": chunk * 10,
            "stage_title": title,
            "stage_role": role,
        },
    )


def test_processing_flow_is_execution_ordered_merged_and_leak_free():
    session = make_session()
    long_text = "先按业务条件圈定当期需要报送的机构大额交易线索，并补齐客户名称等关键辨识要素后继续加工。"
    rows = [
        # 故意乱序写入：第三步、第一步、第二步（同阶段相邻两块应合并）
        _ev("e3", "SP_SAC_A.sql", 30, "汇总与最终输出", "OUTPUT_REPORT", long_text + "最后统一汇总输出。", dedup="按同一客户与账户整行去重"),
        _ev("e1", "SP_SAC_A.sql", 10, "圈定范围", "SCOPE_FILTER", long_text, inc=[{"title": "圈定", "condition": "纳入达到大额门槛的机构交易"}]),
        _ev("e2a", "SP_SAC_A.sql", 20, "分情况处理", "BRANCH_LOGIC", long_text + "不同条线分别核对。",
            br=[{"title": "兜底", "condition": "对手名称为空", "action": "改取开户银行名称"}]),
        _ev("e2b", "SP_SAC_A.sql", 22, "分情况处理", "BRANCH_LOGIC", long_text + "不同条线分别核对并补全。",
            exc=[{"title": "剔除", "condition": "未达到大额门槛的交易"}]),
        # 纯结构定义模板：应被丢弃
        _ev("ddl", "SAC_TABLE_SCHEMAS.sql", 1, None, None, "支撑「机构大额结售汇」的 59 项标准化业务数据台账结构定义。",
            calc="为对应业务领域提供标准化台账登记结构支撑。"),
    ]
    for r in rows:
        session.add(r)
    session.commit()

    steps = summarize_processing_flow(session, [r.id for r in rows], limit=8)

    assert [s["stage"] for s in steps] == ["圈定范围", "分情况处理", "汇总与最终输出"]
    assert [s["order"] for s in steps] == [1, 2, 3]
    assert all(s["flow_label"] == "加工流程一" for s in steps)
    assert [s["stage_role_label"] for s in steps] == ["圈定范围", "分情况处理", "汇总输出"]
    merged = steps[1]
    assert merged["branches"] and merged["exclusions"]

    blob = json.dumps(steps, ensure_ascii=False)
    assert detect_technical_jargon(blob) == []
    for leaked in (".sql", "SP_SAC", "chunk_index", "start_line", "SAC_TABLE_SCHEMAS"):
        assert leaked not in blob
