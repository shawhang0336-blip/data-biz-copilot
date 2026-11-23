"""四阶段受控 AI 流水线端到端测试（Plan-then-Extract 文档/代码规划 × SAC/通用 SQL × 资产融合 × Copilot）。"""

import json
import threading

from app.catalog.synthesizer import ingest_material, load_builtin_sample_pack, synthesize_business_assets
from app.catalog.views import get_asset_panorama
from app.copilot.answer_engine import ask_business_copilot
from app.infra.config import Settings
from app.infra.db_tables import BusinessRuleAssetModel, LogicEvidenceModel, SourceMaterialModel
from app.ingestion.code_parser import parse_code_logic
from app.ingestion.doc_parser import parse_regulatory_document
from app.policy.dc_bus import build_run_bus, detect_technical_jargon

from .helpers import make_session


class _FullPipelineScriptedLlm:
    """模拟 Plan-then-Extract 全流程 AI 模型，验证先看全局结构后按块精炼与 Bus 去技术化闭环。"""

    model = "deepseek-flash"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.tool_history = []
        self.feedbacks = []
        self.doc_plan_payloads = []
        self.doc_extract_payloads = []
        self.code_plan_payloads = []
        self.code_extract_payloads = []
        self._doc_plan_calls = 0
        self._doc_calls = 0
        self._code_plan_calls = 0
        self._sql_calls = 0
        self._synth_calls = 0

    def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
        with self._lock:
            self.tool_history.append(tool_name)
            if feedback:
                self.feedbacks.append((tool_name, feedback))

            if tool_name == "plan_document_structure":
                self._doc_plan_calls += 1
                self.doc_plan_payloads.append(payload)
                sections = payload.get("sections") or []
                if self._doc_plan_calls == 1:
                    return {
                        "document_kind": "REGULATORY_SPEC",
                        "document_summary": "依据 SF_SAC_DOC_TMP 核对监管章节目录。",
                        "chunking_strategy": "HEADING_SECTIONS",
                        "canonical_domains": payload.get("canonical_domains") or ["大额与可疑外汇交易报送"],
                        "section_assignments": [
                            {
                                "section_index": s["section_index"],
                                "role": "RULE_CLAUSE",
                                "business_domain": s.get("draft_domain") or "大额与可疑外汇交易报送",
                                "chunk_group": 0,
                            }
                            for s in sections
                        ],
                    }, {"provider": "test", "model": "deepseek-flash"}

                assignments = []
                for s in sections:
                    title = str(s.get("chapter") or s.get("title") or "")
                    if "前言" in title or "编者按" in title:
                        assignments.append(
                            {
                                "section_index": s["section_index"],
                                "role": "IGNORE",
                                "business_domain": s.get("draft_domain") or "通用业务规范",
                                "chunk_group": 0,
                                "reason": "文档前言编者按，不形成业务规则",
                            }
                        )
                    else:
                        assignments.append(
                            {
                                "section_index": s["section_index"],
                                "role": "CALIBER_DEF" if s.get("draft_kind") == "CALIBER" else "RULE_CLAUSE",
                                "business_domain": s.get("draft_domain") or "大额与可疑外汇交易报送",
                                "chunk_group": 0,
                                "reason": "核心业务规范条款",
                            }
                        )
                return {
                    "document_kind": "REGULATORY_SPEC",
                    "document_summary": "明确大额外汇结售汇境内客户纳入与特殊主体剔除要求。",
                    "chunking_strategy": "HEADING_SECTIONS",
                    "canonical_domains": payload.get("canonical_domains") or ["大额与可疑外汇交易报送"],
                    "section_assignments": assignments,
                }, {"provider": "test", "model": "deepseek-flash"}

            if tool_name == "extract_regulatory_clauses":
                self._doc_calls += 1
                self.doc_extract_payloads.append(payload)
                sections = payload.get("sections") or []
                if self._doc_calls == 1:
                    return {
                        "clauses": [
                            {
                                "section_index": sections[0]["section_index"],
                                "keep": True,
                                "business_domain": "资本项目核验",
                                "clause_title": "误泄物理表名测试",
                                "clause_kind": "INCLUSION",
                                "business_requirement": "查询 SF_SAC_ACCOUNT_INFO 核验资本账户。",
                                "audience": "业务",
                            }
                        ]
                    }, {"provider": "test", "model": "deepseek-flash"}
                clauses = []
                for sec in sections:
                    title = str(sec.get("title") or sec.get("chapter") or "")
                    req_text = str(sec.get("content") or title)
                    clauses.append(
                        {
                            "section_index": sec["section_index"],
                            "keep": True,
                            "business_domain": sec.get("draft_domain") or "大额与可疑外汇交易报送",
                            "clause_title": f"【AI精炼】{title}"[:60],
                            "clause_kind": sec.get("draft_kind") or "INCLUSION",
                            "business_requirement": f"由制度条款AI精炼确认：{req_text[:120]}",
                            "audience": "业务 / 合规",
                            "metrics_mentioned": [],
                        }
                    )
                return {"clauses": clauses}, {"provider": "test", "model": "deepseek-flash"}

            if tool_name == "plan_code_structure":
                self._code_plan_calls += 1
                self.code_plan_payloads.append(payload)
                steps = payload.get("steps") or []
                if self._code_plan_calls == 1:
                    return {
                        "business_overview": "先写入 TMP_SAC_DAILY_MID 再汇总报送。",
                        "canonical_domain": (payload.get("canonical_domain_hints") or ["大额与可疑外汇交易报送"])[0],
                        "chunk_groups": [
                            {
                                "group_id": 0,
                                "stage_title": "全流程阶段",
                                "business_domain": (payload.get("canonical_domain_hints") or ["大额与可疑外汇交易报送"])[0],
                                "stage_role": "SCOPE_FILTER",
                                "step_indices": [s["step_index"] for s in steps],
                                "stage_purpose": "整合筛选与输出。",
                            }
                        ],
                    }, {"provider": "test", "model": "deepseek-flash"}

                canonical = (payload.get("canonical_domain_hints") or ["大额与可疑外汇交易报送"])[0]
                if len(steps) >= 2:
                    mid = max(1, len(steps) - 1)
                    groups = [
                        {
                            "group_id": 0,
                            "stage_title": "主体圈定与异常剔除阶段",
                            "business_domain": canonical,
                            "stage_role": "SCOPE_FILTER",
                            "step_indices": [s["step_index"] for s in steps[:mid]],
                            "stage_purpose": "圈定当期有效交易与核心客户清单。",
                        },
                        {
                            "group_id": 1,
                            "stage_title": "汇总口径核验输出阶段",
                            "business_domain": canonical,
                            "stage_role": "CALCULATION",
                            "step_indices": [s["step_index"] for s in steps[mid:]],
                            "stage_purpose": "承接上游有效明细按客户统计实付金额。",
                        },
                    ]
                else:
                    groups = [
                        {
                            "group_id": 0,
                            "stage_title": "单阶段处理",
                            "business_domain": canonical,
                            "stage_role": "SCOPE_FILTER",
                            "step_indices": [s["step_index"] for s in steps],
                            "stage_purpose": "处理核心过滤口径。",
                        }
                    ]
                return {
                    "business_overview": "先筛除作废与豁免记录形成有效交易集，再按客户统一归集申报净额。",
                    "canonical_domain": canonical,
                    "chunk_groups": groups,
                }, {"provider": "test", "model": "deepseek-flash"}

            if tool_name == "translate_logic_evidence":
                self._sql_calls += 1
                self.code_extract_payloads.append(payload)
                if self._sql_calls == 1:
                    return {
                        "business_domain": payload.get("business_domain_hint") or "外汇收支申报",
                        "source_summary": "从 SAC_FX_LARGE_TXN JOIN CUST_INFO 筛选交易。",
                        "calculation_summary": "按实际申报金额汇总。",
                        "inclusions": [],
                        "exclusions": [],
                        "branches": [],
                        "evidence_ids": payload.get("allowed_evidence_ids", []),
                    }, {"provider": "test", "model": "deepseek-flash"}
                draft = payload.get("deterministic_draft") or (payload.get("draft_evidences") or [{}])[0]
                inclusions = list(draft.get("inclusions") or draft.get("inclusion_predicates") or [])
                exclusions = list(draft.get("exclusions") or draft.get("exclusion_predicates") or [])
                inclusions.append(
                    {
                        "rule_code": "INC_MATCH_CARD_NUMBER",
                        "title": "按主卡有效卡号纳入关联申报",
                        "condition": "交易凭证已绑定有效客户及卡号关联标识",
                        "rationale": "确保个人银行卡涉外交易可准确归集至申报主体。",
                    }
                )
                exclusions.append(
                    {
                        "rule_code": "EXC_AUTO_WHEN_MANUAL_EXISTS",
                        "title": "人工已申报时自动跳过重复生成",
                        "condition": "当期已存在经人工确认提交的同类业务报送记录",
                        "rationale": "防止系统自动生成与人工补录发生重复报送。",
                    }
                )
                return {
                    "business_domain": payload.get("business_domain_hint") or payload.get("domain") or "外汇收支申报",
                    "source_summary": "【AI业务译注】系统在当期申报日内自动纳入达标大额结售汇交易，并排除已撤回及特殊豁免账户。",
                    "calculation_summary": "【AI口径译注】按客户单笔折算人民币交易实付净额直传，不做跨日累计。",
                    "inclusions": inclusions,
                    "exclusions": exclusions,
                    "branches": draft.get("branches") or draft.get("branch_cases") or [],
                    "inclusion_rules": inclusions,
                    "exclusion_rules": exclusions,
                    "branch_cases": draft.get("branches") or draft.get("branch_cases") or [],
                    "evidence_ids": payload.get("allowed_evidence_ids", []),
                }, {"provider": "test", "model": "deepseek-flash"}

            if tool_name == "synthesize_domain_assets":
                self._synth_calls += 1
                if self._synth_calls == 1:
                    return {
                        "description": "使用 TBL_SAC_DAILY_SUMMARY 自动计算日终汇总。",
                        "scope_summary": "覆盖全部渠道流水。",
                        "rules": [],
                        "calibers": [],
                    }, {"provider": "test", "model": "deepseek-flash"}
                rules_out = []
                for r in (payload.get("draft_rules") or [])[:3]:
                    rules_out.append(
                        {
                            "code": r["code"],
                            "title": r["title"],
                            "rule_category": r["rule_category"],
                            "business_condition": f"【AI融合阐释】{r['business_condition']}",
                            "business_action": r["business_action"],
                            "business_rationale": r["business_rationale"],
                            "evidence_ids": r.get("evidence_ids") or [],
                        }
                    )
                cals_out = []
                for c in (payload.get("draft_calibers") or [])[:2]:
                    cals_out.append(
                        {
                            "code": c["code"],
                            "metric_name": c["metric_name"],
                            "business_definition": f"【AI融合定义】{c['business_definition']}",
                            "formula_summary": c["formula_summary"],
                            "distinction_notes": c.get("distinction_notes") or "",
                            "conflict_notes": c.get("conflict_notes") or "",
                            "evidence_ids": c.get("evidence_ids") or [],
                        }
                    )
                return {
                    "description": f"【AI全量融合概览】{payload.get('draft_capability', {}).get('description', '')}",
                    "scope_summary": f"【AI边界说明】{payload.get('draft_capability', {}).get('scope_summary', '')}",
                    "rules": rules_out,
                    "calibers": cals_out,
                }, {"provider": "test", "model": "deepseek-flash"}

            if tool_name == "plan_copilot_tools":
                return {
                    "tool_calls": [
                        {"tool": "get_business_scope_and_rules", "args": {"topic": "大额外汇"}},
                        {"tool": "check_regulatory_coverage_gap", "args": {}},
                    ],
                    "reasoning_summary": "结合用户关于运行情况的口语化提问，调用规则范围与监管差距盘点工具。",
                }, {"provider": "test", "model": "deepseek-flash"}

            if tool_name == "compose_business_answer":
                return {
                    "answer": "当前系统已经正式包含并落实了大额外汇申报核验逻辑，在申报窗口内自动筛除特殊豁免主体；同时对监管要求中尚未纳入系统的专项规则已如实标注提示。",
                    "cited_asset_codes": (payload.get("allowed_asset_codes") or [])[:2],
                    "follow_up_questions": ["哪些监管新规目前仅在文档中体现？"],
                }, {"provider": "test", "model": "deepseek-flash"}

        raise AssertionError(f"Unexpected tool call: {tool_name}")


def test_plan_then_extract_for_doc_and_multistep_sac_and_generic_sql():
    llm = _FullPipelineScriptedLlm()
    bus = build_run_bus({"max_llm_calls": 60, "max_retries": 3, "max_total_tokens": 500000})

    # 1) 文档 Plan-then-Extract：先 plan_document_structure 去噪归域，再带上下文逐批精炼
    doc_md = (
        "# 前言与编者按\n\n本文件仅作内部修订说明，不包含具体规则。\n\n"
        "# 大额外汇交易境内主体申报范围规范\n\n银行应对单日累计结售汇达到申报起点的境内客户纳入当期上报清单。"
    ).encode("utf-8")
    clauses = parse_regulatory_document("sac_doc.md", doc_md, llm=llm, bus=bus)
    assert len(clauses) == 1
    assert clauses[0].clause_title.startswith("【AI精炼】")
    assert clauses[0].source_locator.get("plan_stage") == "PLAN_THEN_EXTRACT"
    assert clauses[0].source_locator.get("plan_applied") is True
    assert "大额外汇" in (clauses[0].source_locator.get("doc_plan_summary") or "")
    # Plan 阶段应先于 extract 触发，且被标为 IGNORE 的前言不得再送入条款提取
    doc_plan_idx = llm.tool_history.index("plan_document_structure")
    doc_ext_idx = llm.tool_history.index("extract_regulatory_clauses")
    assert doc_plan_idx < doc_ext_idx
    assert any(t == "plan_document_structure" and "去技术化门禁拦截" in fb for t, fb in llm.feedbacks)
    assert any(t == "extract_regulatory_clauses" and "去技术化门禁拦截" in fb for t, fb in llm.feedbacks)
    extracted_chapters = [s.get("chapter") for p in llm.doc_extract_payloads for s in (p.get("sections") or [])]
    assert "前言与编者按" not in extracted_chapters
    assert all("document_plan" in p for p in llm.doc_extract_payloads)

    # 2) 多步 SAC 脚本 Plan-then-Extract（含步间血缘 upstream_steps 且零物理表名泄露给 AI）
    sac_multi_sql = """
    /* 程序描述: 大额外汇申报境内客户两阶段筛选与汇总 */
    INSERT INTO TMP_SAC_FX_MID (CUST_NO, TXN_AMT)
    SELECT a.CUST_NO, a.TXN_AMT
    FROM SAC_FX_LARGE_TXN a
    WHERE a.TXN_DT = '2026-03-01'
      AND a.STATUS <> 'CANCELLED';

    INSERT INTO RPT_SAC_FX_DAILY (CUST_NO, TOTAL_AMT)
    SELECT m.CUST_NO, SUM(m.TXN_AMT) AS TOTAL_AMT
    FROM TMP_SAC_FX_MID m
    GROUP BY m.CUST_NO;
    """.encode("utf-8")
    sac_evs = parse_code_logic("sac_fx_multi.sql", sac_multi_sql, llm=llm, bus=bus)
    assert len(sac_evs) == 2
    assert llm.tool_history.index("plan_code_structure") < llm.tool_history.index("translate_logic_evidence")
    assert any(t == "plan_code_structure" and "去技术化门禁拦截" in fb for t, fb in llm.feedbacks)
    assert any(t == "translate_logic_evidence" and "去技术化门禁拦截" in fb for t, fb in llm.feedbacks)

    sac_plan_payload = llm.code_plan_payloads[-1]
    sac_plan_json = json.dumps(sac_plan_payload, ensure_ascii=False)
    for leaked_tbl in ("TMP_SAC_FX_MID", "SAC_FX_LARGE_TXN", "RPT_SAC_FX_DAILY"):
        assert leaked_tbl not in sac_plan_json
    assert sac_plan_payload["steps"][1]["upstream_steps"] == [0]
    assert any((e.internal_locator or {}).get("plan_stage") == "PLAN_THEN_EXTRACT" for e in sac_evs)
    assert all((e.internal_locator or {}).get("plan_applied") is True for e in sac_evs)
    assert all((e.internal_locator or {}).get("ai_refined") is True for e in sac_evs)
    assert any((c_p.get("code_plan") or {}).get("upstream_stages") for c_p in llm.code_extract_payloads)

    # 3) 陌生多步非 SAC SQL 同样走 Plan-then-Extract，而非割裂逐块看树不见森林
    generic_multi_sql = """
    /* 程序描述: 净值理财赎回两阶段清算 */
    INSERT INTO WM_STAGE_ORDERS (investor_id, redeem_amt)
    SELECT o.investor_id, o.redeem_units * o.nav_price
    FROM wm_redeem_orders o
    WHERE o.audit_status <> 'REJECTED';

    INSERT INTO wm_settlement_ledger (investor_id, settle_total)
    SELECT s.investor_id, SUM(s.redeem_amt)
    FROM WM_STAGE_ORDERS s
    GROUP BY s.investor_id;
    """.encode("utf-8")
    gen_evs = parse_code_logic("wm_multi.sql", generic_multi_sql, llm=llm, bus=bus)
    assert len(gen_evs) == 2
    assert all((e.internal_locator or {}).get("plan_applied") is True for e in gen_evs)
    assert all((e.internal_locator or {}).get("ai_refined") is True for e in gen_evs)
    for ev in sac_evs + gen_evs:
        assert detect_technical_jargon(ev.source_summary) == []
        assert detect_technical_jargon(ev.calculation_summary) == []


def test_ai_full_four_stage_pipeline_synthesis_and_copilot(tmp_path):
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    llm = _FullPipelineScriptedLlm()

    summary = load_builtin_sample_pack(session, settings=settings, llm=llm)
    assert summary["ai_enabled"] is True
    assert summary["llm_model"] == "deepseek-flash"
    assert summary["ai_enriched_domains"] >= 1
    assert "plan_document_structure" in llm.tool_history
    assert "plan_code_structure" in llm.tool_history
    assert "synthesize_domain_assets" in llm.tool_history
    assert any(t == "synthesize_domain_assets" and "去技术化门禁拦截" in fb for t, fb in llm.feedbacks)

    materials = session.query(SourceMaterialModel).all()
    assert any("AI 结构规划" in m.summary or "AI 逻辑骨架规划" in m.summary for m in materials)

    pano = get_asset_panorama(session)
    assert any("【AI全量融合概览】" in c["description"] for c in pano["capabilities"])
    assert any("【AI融合阐释】" in r["business_condition"] for r in pano["rules"])

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
    assert detect_technical_jargon(json.dumps(pano["rules"], ensure_ascii=False)) == []
    assert all("exists" not in r["code"] and "number" not in r["code"] for r in pano["rules"])
    assert "SAC_" not in serialized

    # 验证即便实证中原始 rule_code 含 SQL/类型词（如 exists/number），合成规则 code 也会自动哈希脱敏
    ev_row = session.query(LogicEvidenceModel).first()
    assert ev_row is not None
    ev_row.inclusion_predicates_json = list(ev_row.inclusion_predicates_json or []) + [
        {
            "rule_code": "INC_MATCH_CARD_NUMBER",
            "title": "银行卡号匹配纳入验证",
            "condition": "有效主卡关联交易纳入范围",
            "rationale": "验证包含 number 的规则编码安全哈希。",
        }
    ]
    ev_row.exclusion_predicates_json = list(ev_row.exclusion_predicates_json or []) + [
        {
            "rule_code": "EXC_AUTO_WHEN_MANUAL_EXISTS",
            "title": "人工优先自动剔除验证",
            "condition": "已存在人工确认记录时跳过自动生成",
            "rationale": "验证包含 exists 的规则编码安全哈希。",
        }
    ]
    session.flush()
    resynth = synthesize_business_assets(session, settings=settings, llm=llm)
    assert resynth["rule_count"] >= summary["rule_count"]
    pano2 = get_asset_panorama(session)
    assert detect_technical_jargon(json.dumps(pano2["rules"], ensure_ascii=False)) == []
    assert all("exists" not in r["code"] and "number" not in r["code"] for r in pano2["rules"])

    ans = ask_business_copilot(
        session,
        "帮我看一下目前系统整体运行跟监管要求对得上吗，有哪些大额申报规则？",
        settings=settings,
        llm=llm,
    )
    assert "plan_copilot_tools" in llm.tool_history
    assert "compose_business_answer" in llm.tool_history
    assert ans["verification"]["ai_powered"] is True
    assert ans["verification"]["tool_planned_by_ai"] is True
    assert ans["verification"]["llm_model"] == "deepseek-flash"
    assert ans["verification"]["de_jargon_passed"] is True
    assert ans["verification"]["residual_jargon"] == []
    assert ans["tool_trace"][0]["tool"] == "get_business_scope_and_rules"
    assert ans["tool_trace"][1]["tool"] == "check_regulatory_coverage_gap"


def test_synthesize_business_assets_direct_ai_call_without_llm_arg_fallback_safety(tmp_path):
    """验证单独先入库再调用 synthesize_business_assets(..., llm=llm) 同样走 AI 融合并支持故障降级。"""
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    ingest_material(
        session,
        title="custom_reg.md",
        material_kind="REGULATORY_DOC",
        file_type="md",
        content="# 特殊账户报送口径\n\n发生状态变更时按最新有效记录报送。".encode("utf-8"),
        settings=settings,
    )
    ingest_material(
        session,
        title="custom_sql.sql",
        material_kind="CODE_LOGIC",
        file_type="sql",
        content="INSERT INTO TGT_ACCT SELECT CUST_ID, BAL FROM SRC_ACCT WHERE STAT <> 'CLOSED';".encode("utf-8"),
        settings=settings,
    )
    session.commit()

    class _FailingSynthLlm:
        model = "broken-model"

        def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
            raise RuntimeError("simulated network outage")

    summary = synthesize_business_assets(session, settings=settings, llm=_FailingSynthLlm())
    assert summary["ai_enabled"] is True
    assert summary["ai_enriched_domains"] == 0
    assert summary["capability_count"] >= 1
    assert session.query(BusinessRuleAssetModel).count() >= 1


def test_copilot_tool_topk_scoped_gap_disclosure_and_sac_multidomain_preservation(tmp_path):
    """验证规则检索 Top-K 限流与排除意图加权、缺口披露去重防串台、以及 SAC 单脚本多域不被覆盖。"""
    from app.copilot.business_tools import execute_business_tool
    from app.ingestion.ast_extractor import plan_and_extract_script_evidences
    from app.infra.contracts import ExtractedLogicEvidence
    from app.infra.db_tables import BusinessCapabilityModel

    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    load_builtin_sample_pack(session, settings=settings)

    # 1) 测试多规则场景下 get_business_scope_and_rules 限流 <= 15 且 EXCLUSION 优先靠前
    cap = session.query(BusinessCapabilityModel).first()
    assert cap is not None
    for i in range(20):
        session.add(
            BusinessRuleAssetModel(
                id=f"extra-inc-{i}",
                capability_id=cap.id,
                code=f"rule-extra-inc-{i}",
                title=f"结售汇客户大额申报常规纳入规则-{i}",
                rule_category="INCLUSION",
                business_condition="结售汇客户申报金额达到申报门槛",
                business_action="纳入正常报送",
                business_rationale="常规测试条款",
                reconcile_status="VERIFIED",
                evidence_ids_json=[],
                clause_ids_json=[],
                audience="业务",
                status="DRAFT",
                current_version=1,
            )
        )
    session.add(
        BusinessRuleAssetModel(
            id="extra-exc-priority",
            capability_id=cap.id,
            code="rule-extra-exc-priority",
            title="结售汇客户特殊排除规则",
            rule_category="EXCLUSION",
            business_condition="属于结售汇测试或豁免客户时排除",
            business_action="直接排除不予报送",
            business_rationale="排除意图应优先召回",
            reconcile_status="VERIFIED",
            evidence_ids_json=[],
            clause_ids_json=[],
            audience="业务",
            status="DRAFT",
            current_version=1,
        )
    )
    session.flush()

    topk_res = execute_business_tool(session, "get_business_scope_and_rules", {"topic": "结售汇客户"})
    assert len(topk_res["rules"]) == 15
    assert topk_res["total_matched"] >= 21

    scope_res = execute_business_tool(session, "get_business_scope_and_rules", {"topic": "结售汇哪些客户会被排除"})
    assert 1 <= len(scope_res["rules"]) <= 15
    assert scope_res["rules"][0]["rule_category"] == "EXCLUSION"

    # 2) check_regulatory_coverage_gap 带 topic 时只披露与该主题相关的问题，不把无关未落地领域强塞到顶部
    gap_scoped = execute_business_tool(session, "check_regulatory_coverage_gap", {"topic": "联名账户 折算 分歧"})
    assert "期货期权" not in (gap_scoped.get("disclose_text") or "")
    assert "应报未报" not in (gap_scoped.get("disclose_text") or "")
    if gap_scoped.get("disclose"):
        assert gap_scoped["disclose_kind"] == "divergent"

    # 3) 验证跨多域 SAC 脚本经 plan_and_extract_script_evidences 后仍保留各自确定性域归属
    ev_main = ExtractedLogicEvidence(
        logic_fingerprint="fp-sac-multi-0",
        business_domain_hint="大额与可疑外汇交易报送",
        target_concept_hint="大额涉外交易圈定",
        source_summary="圈定大额外汇申报主数据。",
        inclusion_predicates=[],
        exclusion_predicates=[],
        branch_cases=[],
        calculation_kind="DIRECT",
        calculation_summary="按原始发生额报送。",
        dedup_grain_hint="",
        internal_locator={"script": "SP_SAC_ACCT_AMOUNT.sql", "chunk_index": 0, "statement_kind": "INSERT", "join_count": 1, "source_tables": ["A"], "target_tables": ["B"]},
    )
    ev_unrep = ExtractedLogicEvidence(
        logic_fingerprint="fp-sac-multi-1",
        business_domain_hint="应报未报漏报风险识别",
        target_concept_hint="应报未报风险清单",
        source_summary="生成应报未报漏报风险账户清单并供下游剔除。",
        inclusion_predicates=[],
        exclusion_predicates=[],
        branch_cases=[],
        calculation_kind="DIRECT",
        calculation_summary="统计应报未报清单。",
        dedup_grain_hint="",
        internal_locator={"script": "SP_SAC_ACCT_AMOUNT.sql", "chunk_index": 1, "statement_kind": "INSERT", "join_count": 1, "source_tables": ["B"], "target_tables": ["C"]},
    )

    class _BroadSingleDomainPlanLlm:
        def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
            if tool_name == "plan_code_structure":
                return {
                    "business_overview": "大额交易圈定与应报未报识别联合处理。",
                    "canonical_domain": "大额与可疑外汇交易报送",
                    "chunk_groups": [
                        {
                            "group_id": 0,
                            "stage_title": "整合阶段",
                            "business_domain": "大额与可疑外汇交易报送",
                            "stage_role": "SCOPE_FILTER",
                            "step_indices": [0, 1],
                            "stage_purpose": "混合阶段",
                        }
                    ],
                }, {"provider": "test", "model": "test"}
            return {
                "business_domain": "大额与可疑外汇交易报送",
                "source_summary": "完成涉外交易圈定及应报未报风险识别处理。",
                "calculation_summary": "按笔据实传出。",
            }, {"provider": "test", "model": "test"}

    bus = build_run_bus({"max_llm_calls": 20, "max_retries": 2, "max_total_tokens": 200000})
    out_evs = plan_and_extract_script_evidences(
        "SP_SAC_ACCT_AMOUNT.sql",
        "涉外大额及应报未报统计",
        [ev_main, ev_unrep],
        [],
        is_sac_script=True,
        llm=_BroadSingleDomainPlanLlm(),
        bus=bus,
    )
    assert out_evs[0].business_domain_hint == "大额与可疑外汇交易报送"
    assert out_evs[1].business_domain_hint == "应报未报漏报风险识别"
