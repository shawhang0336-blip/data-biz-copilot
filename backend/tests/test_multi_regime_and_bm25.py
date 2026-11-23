"""多监管体系（1104 / EAST / SAFE / PLUS / 一表通）、PDF 解析与纯 Python 中文 N-gram BM25 检索测试。"""

import json
import subprocess

from app.catalog.synthesizer import ingest_material, synthesize_business_assets
from app.catalog.views import get_asset_panorama
from app.copilot.answer_engine import ask_business_copilot
from app.copilot.business_tools import execute_business_tool
from app.copilot.disclosure import DISCLOSURE_TEXT
from app.infra.config import Settings
from app.infra.retrieval import bm25_score_documents, infer_regulatory_regime, tokenize_for_search
from app.ingestion.doc_parser import parse_regulatory_document
from app.policy.dc_bus import detect_technical_jargon

from .helpers import make_session


def _build_minimal_pdf(lines: list[str]) -> bytes:
    """构造最小合法 PDF-1.4 字节流，确保原生调用本机 pdftotext 可真实抽取文本。"""
    escaped = [
        line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        for line in lines
    ]
    ops = ["BT", "/F1 12 Tf", "72 720 Td"]
    for idx, line in enumerate(escaped):
        if idx > 0:
            ops.append("0 -18 Td")
        ops.append(f"({line}) Tj")
    ops.append("ET")
    stream_bytes = "\n".join(ops).encode("latin-1")

    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
        ),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        (
            f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream\nendobj\n"
        ),
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(buf))
        buf.extend(obj)
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(objs) + 1}\n".encode("ascii"))
    buf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        (
            f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(buf)


def test_retrieval_regime_detection_and_bm25_zero_jargon_deps():
    assert infer_regulatory_regime("1104_G01_填报说明.pdf") == "1104"
    assert infer_regulatory_regime("EAST5.0_对公贷款.sql") == "EAST"
    assert infer_regulatory_regime("SAFE_FAL_BOP_涉外收支.md") == "SAFE"
    assert infer_regulatory_regime("PLUS平台手工补录核验.sql") == "PLUS"
    assert infer_regulatory_regime("一表通互联网贷款映射.md") == "一表通"
    assert infer_regulatory_regime("中国人民银行大额交易报告.md") == "人行统计"

    tokens = tokenize_for_search("1104 G01 同业存放余额怎么算？")
    assert "1104" in tokens
    assert "g01" in tokens
    assert "同业" in tokens
    assert "存放" in tokens

    docs = [
        {
            "title": "1104 G01 同业存放余额核算规则",
            "regime": "1104",
            "body": "统计期末境内外同业存放款项含拆放扣除已质押定期存单",
        },
        {
            "title": "EAST 对公贷款五级分类不良剔除",
            "regime": "EAST",
            "body": "剔除表外承兑垫款与测试贷款账号后输出逾期天数字段",
        },
        {
            "title": "PLUS 手工补录覆盖主表数据规则",
            "regime": "PLUS",
            "body": "若存在经审批生效的人工补录确认值，优先覆盖系统自动采集值",
        },
    ]
    regimes = [d["regime"] for d in docs]
    scores_1104 = bm25_score_documents(
        "1104 G01 同业存放期末口径怎么算",
        docs,
        field_weights={"title": 2.5, "regime": 1.5, "body": 1.0},
        regimes=regimes,
    )
    assert scores_1104[0] > scores_1104[1]
    assert scores_1104[0] > scores_1104[2]

    scores_plus = bm25_score_documents(
        "PLUS 手工补录怎么覆盖自动采集",
        docs,
        field_weights={"title": 2.5, "regime": 1.5, "body": 1.0},
        regimes=regimes,
    )
    assert scores_plus[2] > scores_plus[0]
    assert scores_plus[2] > scores_plus[1]

    scores_unrelated = bm25_score_documents(
        "期货期权保证金日间强平规则是什么？",
        docs,
        field_weights={"title": 2.5, "regime": 1.5, "body": 1.0},
        regimes=regimes,
    )
    assert scores_unrelated == [0.0, 0.0, 0.0]


def test_pdf_ingestion_with_real_pdftotext_or_clean_mock(monkeypatch):
    # 1) 验证真实本机 pdftotext 路径
    pdf_bytes = _build_minimal_pdf(
        [
            "# 1104 G01 Interbank Placement Spec",
            "## Scope and Exclusion Rule",
            "Include active interbank placements and exclude test accounts.",
        ]
    )
    clauses = parse_regulatory_document("1104_G01_spec.pdf", pdf_bytes)
    assert len(clauses) >= 1
    assert clauses[0].source_locator.get("regime") == "1104"
    assert clauses[0].business_domain != "大额与可疑外汇交易报送"
    assert "interbank placements" in clauses[0].business_requirement

    # 2) 验证中文监管 PDF 文本解析（模拟 pdftotext 返回常见中文双栏/条款文本）
    cn_text = (
        "# 1104 非现场监管报表 G01 填报说明\n"
        "## 同业存放期末净额口径与剔除规则\n"
        "纳入报告期末存续的境内金融机构同业存放款项，剔除保证金专用及已到期清零户。"
    )

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=cn_text, stderr="")

    monkeypatch.setattr("app.ingestion.doc_parser.subprocess.run", _fake_run)
    cn_clauses = parse_regulatory_document("G01填报制度.pdf", b"%PDF-1.4 dummy")
    assert len(cn_clauses) >= 1
    assert cn_clauses[0].source_locator.get("regime") == "1104"
    assert "同业存放" in cn_clauses[0].business_domain or "非现场监管" in cn_clauses[0].business_domain
    assert cn_clauses[0].business_domain != "大额与可疑外汇交易报送"


def test_multi_regime_1104_east_safe_plus_coexist_without_sac_bias_and_answer_via_bm25(tmp_path):
    """验证 1104 / EAST / SAFE / PLUS 四大体系文档+SQL 同时接入时互不误归类，且 BM25 精准召回并零黑话问答。"""
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))

    materials = [
        (
            "REGULATORY_DOC",
            "md",
            "1104_G01_同业存放规范.md",
            (
                "# 1104 G01 同业存放与拆放核算\n\n"
                "## 同业存放款项纳入与剔除口径\n"
                "纳入境内银行业及非银金融机构活期和定期同业存放款项，剔除内部往来清算过渡及测试账户。\n\n"
                "## 同业存放净额计算口径\n"
                "按统计期末账面余额扣减已冻结质押定期存单后的净额填报，不跨币种汇总。"
            ).encode("utf-8"),
        ),
        (
            "CODE_LOGIC",
            "sql",
            "proc_1104_g01_interbank.sql",
            (
                "/* 报表名称: 1104 G01 同业存放与拆放核算 */\n"
                "INSERT INTO RPT_1104_G01_INTERBANK (ACCT_NO, CUST_TYPE, NET_BAL)\n"
                "SELECT a.ACCT_NO, a.CUST_TYPE, SUM(a.BOOK_BAL - a.PLEDGE_AMT) AS NET_BAL\n"
                "FROM CORE_INTERBANK_ACCT a\n"
                "WHERE a.RPT_DATE = '2026-06-30'\n"
                "  AND a.STATUS <> 'TEST'\n"
                "  AND NOT EXISTS (SELECT 1 FROM CORE_INTERNAL_CLEAR c WHERE c.ACCT_NO = a.ACCT_NO)\n"
                "GROUP BY a.ACCT_NO, a.CUST_TYPE;"
            ).encode("utf-8"),
        ),
        (
            "REGULATORY_DOC",
            "md",
            "EAST_对公贷款五级分类指引.md",
            (
                "# EAST5.0 对公贷款五级分类统计\n\n"
                "## 不良贷款与核销剔除规则\n"
                "统计对公信贷五级分类为次级、可疑、损失的不良贷款余额，剔除已全额核销及模拟演练合同。"
            ).encode("utf-8"),
        ),
        (
            "CODE_LOGIC",
            "sql",
            "proc_east_corp_loan_grade.sql",
            (
                "/* 业务说明: EAST5.0 对公贷款五级分类统计 */\n"
                "INSERT INTO EAST_CORP_LOAN_GRADE (LOAN_ID, GRADE_CODE, LOAN_BAL)\n"
                "SELECT l.LOAN_ID, l.GRADE_CODE, SUM(l.PRINCIPAL_BAL) AS LOAN_BAL\n"
                "FROM CORE_CORP_LOAN l\n"
                "WHERE l.GRADE_CODE IN ('3', '4', '5')\n"
                "  AND l.WRITE_OFF_FLAG <> 'Y'\n"
                "GROUP BY l.LOAN_ID, l.GRADE_CODE;"
            ).encode("utf-8"),
        ),
        (
            "REGULATORY_DOC",
            "md",
            "SAFE_FAL_涉外收入申报指引.md",
            (
                "# SAFE FAL 国际收支涉外收入申报\n\n"
                "## 涉外收入小额免申报阈值规则\n"
                "对公境内居民通过境内银行从境外收到的款项按涉外收入申报，等值5000美元及以下对私免申报但对公应报尽报。"
            ).encode("utf-8"),
        ),
        (
            "CODE_LOGIC",
            "sql",
            "proc_safe_fal_bop_income.sql",
            (
                "/* 程序描述: SAFE FAL 国际收支涉外收入申报 */\n"
                "INSERT INTO RPT_SAFE_BOP_INCOME (DECL_ID, CUST_KIND, USD_AMT)\n"
                "SELECT t.DECL_ID, t.CUST_KIND, t.USD_AMT\n"
                "FROM FX_INWARD_REMIT t\n"
                "WHERE t.CUST_KIND = 'ORG' OR t.USD_AMT > 5000;"
            ).encode("utf-8"),
        ),
        (
            "REGULATORY_DOC",
            "md",
            "PLUS_手工补录覆盖管理规范.md",
            (
                "# PLUS 手工补录与人工覆盖规范\n\n"
                "## 复核通过补录优先覆盖原则\n"
                "经支行经办提交且由上级合规复核通过的 PLUS 手工补录记录，在出数合并阶段自动覆盖同主键自动采集记录。"
            ).encode("utf-8"),
        ),
        (
            "CODE_LOGIC",
            "sql",
            "proc_plus_manual_override.sql",
            (
                "/* 程序描述: PLUS 手工补录与人工覆盖规范 */\n"
                "MERGE INTO RPT_PLUS_FINAL dst\n"
                "USING (SELECT REC_KEY, FINAL_AMT FROM PLUS_MANUAL_INPUT WHERE AUDIT_STATUS = 'APPROVED') src\n"
                "ON (dst.REC_KEY = src.REC_KEY)\n"
                "WHEN MATCHED THEN UPDATE SET dst.FINAL_AMT = src.FINAL_AMT;"
            ).encode("utf-8"),
        ),
    ]

    for kind, ftype, title, raw in materials:
        ingest_material(
            session,
            title=title,
            material_kind=kind,
            file_type=ftype,
            content=raw,
            settings=settings,
        )
    session.commit()

    summary = synthesize_business_assets(session, settings=settings)
    assert summary["capability_count"] == 4

    pano = get_asset_panorama(session)
    caps_by_name = {c["name"]: c for c in pano["capabilities"]}
    assert "大额与可疑外汇交易报送" not in caps_by_name
    assert set(caps_by_name.keys()) == {
        "1104 G01 同业存放与拆放核算",
        "EAST5.0 对公贷款五级分类统计",
        "SAFE FAL 国际收支涉外收入申报",
        "PLUS 手工补录与人工覆盖规范",
    }
    assert caps_by_name["1104 G01 同业存放与拆放核算"]["regime"] == "1104"
    assert caps_by_name["EAST5.0 对公贷款五级分类统计"]["regime"] == "EAST"
    assert caps_by_name["SAFE FAL 国际收支涉外收入申报"]["regime"] == "SAFE"
    assert caps_by_name["PLUS 手工补录与人工覆盖规范"]["regime"] == "PLUS"

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

    # BM25 跨体系精确问答验证（无需固定 SAC 词表即可召回各自体系资产）
    queries_and_expected = [
        ("1104 G01 同业存放净额怎么算？", "1104 G01 同业存放与拆放核算"),
        ("EAST 对公贷款五级分类不良怎么剔除？", "EAST5.0 对公贷款五级分类统计"),
        ("SAFE 涉外收入小额免申报怎么处理？", "SAFE FAL 国际收支涉外收入申报"),
        ("PLUS 手工补录审核通过怎么覆盖原记录？", "PLUS 手工补录与人工覆盖规范"),
    ]
    for q, expected_domain in queries_and_expected:
        ans = ask_business_copilot(session, q, settings=settings)
        assert ans["verification"]["de_jargon_passed"] is True
        assert ans["verification"]["residual_jargon"] == []
        assert expected_domain in ans["answer"], f"Expected {expected_domain} in answer for: {q}\n{ans['answer']}"

    # 工具直调确认 1104 口径单独精准匹配
    cal_res = execute_business_tool(session, "get_business_caliber_detail", {"metric": "1104 同业存放净额"})
    assert cal_res["calibers"] and cal_res["calibers"][0]["regime"] == "1104"

    # 完全不相关金融词仍可靠返回 not_found 披露，不被 BM25 噪音频召回
    ans_unmatched = ask_business_copilot(session, "期货期权保证金日间强平规则是什么？", settings=settings)
    assert DISCLOSURE_TEXT["not_found"] in ans_unmatched["answer"]


def test_sql_logic_details_in_memory_feeding_jargon_gate_and_multistep_caliber_priority(tmp_path):
    """验证 SQL AST 真实细节仅内存喂给 Stage2 翻译、不落库且不进 Plan；小写列名被拦截；多步口径优先选 AGGREGATE。"""
    from app.infra.db_tables import LogicEvidenceModel
    from app.ingestion.ast_extractor import extract_sql_ast_skeleton
    from app.policy.dc_bus import build_run_bus

    # 1) 验证四大监管典型 SQL 的真实 AST logic_details 抽取
    sk_1104 = extract_sql_ast_skeleton(
        "INSERT INTO RPT_1104_G01 (ACCT_NO, NET_BAL) "
        "SELECT a.ACCT_NO, SUM(a.BOOK_BAL - a.PLEDGE_AMT) AS NET_BAL "
        "FROM CORE_INTERBANK_ACCT a "
        "WHERE a.STATUS <> 'TEST' "
        "  AND NOT EXISTS (SELECT 1 FROM CORE_INTERNAL_CLEAR c WHERE c.ACCT_NO = a.ACCT_NO) "
        "GROUP BY a.ACCT_NO;"
    )
    det_1104 = sk_1104["logic_details"]
    assert any("BOOK_BAL - a.PLEDGE_AMT" in expr or "BOOK_BAL - PLEDGE_AMT" in expr for expr in det_1104["select_expressions"])
    assert any("STATUS <> 'TEST'" in p for p in det_1104["where_predicates"])
    assert any("NOT EXISTS" in p for p in det_1104["where_predicates"])

    sk_east = extract_sql_ast_skeleton(
        "SELECT l.LOAN_ID, SUM(l.PRINCIPAL_BAL) AS BAL FROM CORE_CORP_LOAN l "
        "WHERE l.GRADE_CODE IN ('3', '4', '5') AND l.WRITE_OFF_FLAG <> 'Y' GROUP BY l.LOAN_ID;"
    )
    assert any("GRADE_CODE IN ('3', '4', '5')" in p for p in sk_east["logic_details"]["where_predicates"])
    assert any("WRITE_OFF_FLAG <> 'Y'" in p for p in sk_east["logic_details"]["where_predicates"])

    sk_safe = extract_sql_ast_skeleton(
        "SELECT t.DECL_ID FROM FX_INWARD_REMIT t WHERE t.CUST_KIND = 'ORG' OR t.USD_AMT > 5000;"
    )
    assert any("CUST_KIND = 'ORG' OR" in p and "5000" in p for p in sk_safe["logic_details"]["where_predicates"])

    sk_plus = extract_sql_ast_skeleton(
        "MERGE INTO RPT_PLUS_FINAL dst "
        "USING (SELECT REC_KEY, FINAL_AMT FROM PLUS_MANUAL_INPUT WHERE AUDIT_STATUS = 'APPROVED') src "
        "ON (dst.REC_KEY = src.REC_KEY) "
        "WHEN MATCHED THEN UPDATE SET dst.FINAL_AMT = src.FINAL_AMT;"
    )
    md_plus = sk_plus["logic_details"]["merge_details"]
    assert "REC_KEY" in md_plus["on_condition"]
    assert any("AUDIT_STATUS = 'APPROVED'" in w for w in sk_plus["logic_details"]["where_predicates"])
    assert any("UPDATE" in wc.upper() for wc in md_plus["when_clauses"])

    # 2) 验证 Bus 能拦截小写蛇形字段名（redeem_units / audit_status）与 SUM(/WHEN MATCHED
    bus = build_run_bus({"max_llm_calls": 30, "max_retries": 3, "max_total_tokens": 200000})
    rej_snake = bus.adjudicate(
        "translate_logic_evidence",
        {"source_summary": "按 redeem_units 与 audit_status 判断是否入选。"},
        {},
    )
    assert rej_snake is not None and "redeem_units" in rej_snake
    rej_sum = bus.adjudicate(
        "translate_logic_evidence",
        {"calculation_summary": "直接执行 SUM(余额) 后输出并 WHEN MATCHED 更新。"},
        {},
    )
    assert rej_sum is not None

    # 3) 多步 1104 G01 SQL + PLUS MERGE 单步 SQL：验证 Plan 零泄漏、Extract 收到 sql_logic_context、
    #    内部库不落 sql_snippet/logic_details、且冗余 GEN_* 被清理、口径卡片选中 AGGREGATE 扣减公式且无 SAC 硬编码
    class _InspectionLlm:
        model = "deepseek-flash"

        def __init__(self) -> None:
            self.plan_payloads = []
            self.extract_payloads = []

        def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
            if tool_name == "plan_code_structure":
                self.plan_payloads.append(payload)
                steps = payload.get("steps") or []
                return {
                    "business_overview": "先剔除测试与系统内清算账户，再按净额扣减已质押存单汇总同业存放。",
                    "canonical_domain": "1104 G01 同业存放与拆放核算",
                    "chunk_groups": [
                        {
                            "group_id": 0,
                            "stage_title": "有效同业账户筛选阶段",
                            "business_domain": "1104 G01 同业存放与拆放核算",
                            "stage_role": "SCOPE_FILTER",
                            "step_indices": [0],
                            "stage_purpose": "剔除测试户与系统内清算过渡户",
                        },
                        {
                            "group_id": 1,
                            "stage_title": "净额扣减汇总核算阶段",
                            "business_domain": "1104 G01 同业存放与拆放核算",
                            "stage_role": "CALCULATION",
                            "step_indices": [s["step_index"] for s in steps[1:]] or [0],
                            "stage_purpose": "按账面余额扣减质押存单输出同业存放净额",
                        },
                    ],
                }, {"provider": "test", "model": "deepseek-flash"}

            if tool_name == "translate_logic_evidence":
                self.extract_payloads.append(payload)
                ctx_json = json.dumps(payload.get("sql_logic_context") or {}, ensure_ascii=False)
                stage_role = (payload.get("code_plan") or {}).get("stage_role")
                if "AUDIT_STATUS" in ctx_json:
                    return {
                        "business_domain_hint": "PLUS 手工补录与人工覆盖规范",
                        "target_concept_hint": "审核通过人工补录覆盖",
                        "source_summary": "仅提取经合规复核确认通过的人工补录台账记录，按唯一业务标识覆盖原自动采集结果。",
                        "calculation_summary": "复核通过后按最新人工确认金额覆盖原记录，未审批记录不生效。",
                        "inclusions": [
                            {
                                "rule_code": "PLUS_APPROVED_ONLY",
                                "title": "仅纳入审核通过的手工补录记录",
                                "condition": "手工补录台账的复核审批状态明确为已通过",
                                "rationale": "确保未经审批确认的手工修改不会覆盖正式监管台账。",
                            }
                        ],
                        "exclusions": [],
                        "branches": [],
                        "dedup_grain_hint": "按唯一业务记录标识覆盖匹配台账，确保同一记录幂等生效。",
                    }, {"provider": "test", "model": "deepseek-flash"}
                if stage_role == "CALCULATION":
                    return {
                        "business_domain_hint": "1104 G01 同业存放与拆放核算",
                        "target_concept_hint": "同业存放净额",
                        "source_summary": "承接有效同业存放明细，按账户属性分组汇总净额。",
                        "calculation_summary": "已落地同业存放净额算法：按同业存放期末账面余额扣减已质押定期存单金额后的净额分组汇总。",
                        "inclusions": [],
                        "exclusions": [],
                        "branches": [],
                    }, {"provider": "test", "model": "deepseek-flash"}
                return {
                    "business_domain_hint": "1104 G01 同业存放与拆放核算",
                    "target_concept_hint": "同业存放有效账户筛选",
                    "source_summary": "从境内外同业存放台账圈定报告期末有效记录，剔除测试户与系统内清算过渡户。",
                    "calculation_summary": "单笔原值暂存筛选，尚待下一步净额汇总。",
                    "inclusions": [
                        {
                            "rule_code": "G01_REPORT_DATE",
                            "title": "限定报告期末当日同业存放台账",
                            "condition": "仅纳入报告日属于当期月末基准日的同业存放账户记录",
                            "rationale": "对齐 1104 期末时点口径要求。",
                        }
                    ],
                    "exclusions": [
                        {
                            "rule_code": "G01_EXC_TEST_CLEAR",
                            "title": "剔除测试账户及系统内往来清算过渡户",
                            "condition": "若账户状态为测试状态或命中系统内往来清算清单，则直接剔除",
                            "rationale": "避免虚增同业存放余额。",
                        }
                    ],
                    "branches": [],
                }, {"provider": "test", "model": "deepseek-flash"}

            if tool_name == "synthesize_domain_assets":
                cap = payload.get("draft_capability") or {}
                cals = [
                    {
                        "code": c["code"],
                        "metric_name": c["metric_name"],
                        "business_definition": c["business_definition"],
                        "formula_summary": c["formula_summary"],
                        "distinction_notes": c.get("distinction_notes") or "",
                        "conflict_notes": c.get("conflict_notes") or "",
                        "evidence_ids": c.get("evidence_ids") or [],
                    }
                    for c in (payload.get("draft_calibers") or [])
                ]
                return {
                    "description": cap.get("description") or "已完成融合",
                    "scope_summary": cap.get("scope_summary") or "已完成边界融合",
                    "rules": payload.get("draft_rules") or [],
                    "calibers": cals,
                }, {"provider": "test", "model": "deepseek-flash"}

            return {"tool_calls": [], "reasoning_summary": ""}, {"provider": "test", "model": "deepseek-flash"}

    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    llm = _InspectionLlm()

    ingest_material(
        session,
        title="1104_G01_spec.md",
        material_kind="REGULATORY_DOC",
        file_type="md",
        content=(
            "# 1104 G01 同业存放与拆放核算\n\n"
            "## 同业存放净额计算口径\n"
            "按统计期末账面余额扣减已冻结质押定期存单后的净额填报。"
        ).encode("utf-8"),
        settings=settings,
    )
    ingest_material(
        session,
        title="proc_1104_g01_two_step.sql",
        material_kind="CODE_LOGIC",
        file_type="sql",
        content=(
            "/* 报表名称: 1104 G01 同业存放与拆放核算 */\n"
            "INSERT INTO TMP_1104_G01_VALID (ACCT_NO, CUST_TYPE, BOOK_BAL, PLEDGE_AMT)\n"
            "SELECT a.ACCT_NO, a.CUST_TYPE, a.BOOK_BAL, a.PLEDGE_AMT\n"
            "FROM CORE_INTERBANK_ACCT a\n"
            "WHERE a.RPT_DATE = '2026-06-30'\n"
            "  AND a.STATUS <> 'TEST'\n"
            "  AND NOT EXISTS (SELECT 1 FROM CORE_INTERNAL_CLEAR c WHERE c.ACCT_NO = a.ACCT_NO);\n"
            "INSERT INTO RPT_1104_G01_INTERBANK (CUST_TYPE, NET_BAL)\n"
            "SELECT t.CUST_TYPE, SUM(t.BOOK_BAL - t.PLEDGE_AMT) AS NET_BAL\n"
            "FROM TMP_1104_G01_VALID t\n"
            "GROUP BY t.CUST_TYPE;"
        ).encode("utf-8"),
        settings=settings,
        llm=llm,
    )
    ingest_material(
        session,
        title="proc_plus_override.sql",
        material_kind="CODE_LOGIC",
        file_type="sql",
        content=(
            "/* 程序描述: PLUS 手工补录与人工覆盖规范 */\n"
            "MERGE INTO RPT_PLUS_FINAL dst\n"
            "USING (SELECT REC_KEY, FINAL_AMT FROM PLUS_MANUAL_INPUT WHERE AUDIT_STATUS = 'APPROVED') src\n"
            "ON (dst.REC_KEY = src.REC_KEY)\n"
            "WHEN MATCHED THEN UPDATE SET dst.FINAL_AMT = src.FINAL_AMT;"
        ).encode("utf-8"),
        settings=settings,
        llm=llm,
    )
    session.commit()

    # Plan 绝不能看到表名/字段名，Extract 必须收到真实 sql_logic_context
    plan_json = json.dumps(llm.plan_payloads, ensure_ascii=False)
    for forbidden in ("CORE_INTERBANK_ACCT", "TMP_1104_G01_VALID", "BOOK_BAL", "PLEDGE_AMT", "sql_logic_context"):
        assert forbidden not in plan_json
    assert any("PLEDGE_AMT" in json.dumps(p.get("sql_logic_context") or {}, ensure_ascii=False) for p in llm.extract_payloads)
    assert any("AUDIT_STATUS" in json.dumps(p.get("sql_logic_context") or {}, ensure_ascii=False) for p in llm.extract_payloads)

    # 持久化入库的 internal_locator_json 不包含 sql_snippet 或 logic_details
    for ev_row in session.query(LogicEvidenceModel).all():
        loc = ev_row.internal_locator_json or {}
        assert "sql_snippet" not in loc
        assert "logic_details" not in loc

    summary = synthesize_business_assets(session, settings=settings, llm=llm)
    assert summary["capability_count"] == 2
    pano = get_asset_panorama(session)
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

    # 1104 规则已被精简且冗余 GEN_* 占位规则已清理
    g01_rules = [r for r in pano["rules"] if r["capability_name"] == "1104 G01 同业存放与拆放核算"]
    assert len(g01_rules) == 2
    g01_cals = [c for c in pano["calibers"] if c["capability_name"] == "1104 G01 同业存放与拆放核算"]
    assert len(g01_cals) == 1
    assert "扣减已质押定期存单" in g01_cals[0]["formula_summary"]
    assert "应报未报" not in g01_cals[0]["exclusion_notes"]
    assert "交易后余额是交易时点静态快照" not in g01_cals[0]["distinction_notes"]


def test_single_step_domain_preservation_and_cross_source_domain_alignment(tmp_path):
    """验证单步具体域名不被括号改写、跨源同名异写安全合并、无关域不误合并、且同域 DEDUP 规则不重复。"""
    import uuid
    from app.infra.db_tables import LogicEvidenceModel, RegulatoryClauseModel, SourceMaterialModel
    from app.ingestion.ast_extractor import translate_evidence_with_llm
    from app.policy.dc_bus import build_run_bus

    # 1) 单步已有具体域名时，不被模型的近义扩写覆盖
    class _ParaphraseDomainLlm:
        model = "deepseek-flash"

        def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
            return {
                "business_domain_hint": "对公贷款五级分类统计（监管报送口径）",
                "target_concept_hint": "对公不良贷款余额",
                "source_summary": "筛选三四五档不良贷款且排除已核销记录。",
                "calculation_summary": "按贷款加分类汇总余额。",
                "inclusions": [],
                "exclusions": [],
                "branches": [],
            }, {"provider": "test", "model": "deepseek-flash"}

    bus = build_run_bus({"max_llm_calls": 10, "max_retries": 2, "max_total_tokens": 100000})
    out = translate_evidence_with_llm(
        {"statement_kind": "INSERT"},
        ["EAST5.0 对公贷款五级分类统计"],
        {
            "business_domain_hint": "EAST5.0 对公贷款五级分类统计",
            "target_concept_hint": "EAST5.0 对公贷款五级分类统计",
            "source_summary": "草稿",
            "inclusions": [],
            "exclusions": [],
            "branches": [],
            "calculation_kind": "AGGREGATE",
            "calculation_summary": "草稿",
            "dedup_grain_hint": "",
        },
        llm=_ParaphraseDomainLlm(),
        bus=bus,
    )
    assert out["business_domain_hint"] == "EAST5.0 对公贷款五级分类统计"

    # 2) synthesize_business_assets 跨源域名安全对齐 + 防误合并 + 同域 DEDUP 去重
    session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    m_doc = SourceMaterialModel(
        id=str(uuid.uuid4()),
        title="reg_docs.md",
        material_kind="REGULATORY_DOC",
        file_type="md",
        sha256="hash-doc-align",
        blob_uri="local://doc",
        item_count=3,
        summary="doc",
    )
    m_code = SourceMaterialModel(
        id=str(uuid.uuid4()),
        title="code_scripts.sql",
        material_kind="CODE_LOGIC",
        file_type="sql",
        sha256="hash-code-align",
        blob_uri="local://code",
        item_count=3,
        summary="code",
    )
    session.add_all([m_doc, m_code])
    session.flush()

    session.add_all(
        [
            RegulatoryClauseModel(
                id=str(uuid.uuid4()),
                material_id=m_doc.id,
                chapter="第一章",
                business_domain="EAST5.0 对公贷款五级分类统计",
                clause_title="对公不良贷款核销剔除",
                clause_kind="EXCLUSION",
                business_requirement="剔除已核销贷款后汇总对公不良贷款。",
                audience="业务",
                metrics_mentioned_json=[],
                source_locator_json={"regime": "EAST"},
            ),
            RegulatoryClauseModel(
                id=str(uuid.uuid4()),
                material_id=m_doc.id,
                chapter="第二章",
                business_domain="SAFE FAL 国际收支涉外收入申报",
                clause_title="涉外收入小额免申报",
                clause_kind="INCLUSION",
                business_requirement="对公全部纳入，对私超五千美元纳入。",
                audience="业务",
                metrics_mentioned_json=[],
                source_locator_json={"regime": "SAFE"},
            ),
            RegulatoryClauseModel(
                id=str(uuid.uuid4()),
                material_id=m_doc.id,
                chapter="第三章",
                business_domain="大额与可疑外汇交易报送",
                clause_title="大额交易申报",
                clause_kind="INCLUSION",
                business_requirement="达到三十万美元门限纳入大额申报。",
                audience="业务",
                metrics_mentioned_json=[],
                source_locator_json={"regime": "SAFE"},
            ),
        ]
    )
    session.add_all(
        [
            LogicEvidenceModel(
                id=str(uuid.uuid4()),
                material_id=m_code.id,
                logic_fingerprint="fp-east-paraphrase-1",
                business_domain_hint="对公贷款五级分类统计（监管报送口径）",
                target_concept_hint="不良贷款余额",
                source_summary="排除已核销标记记录，按分类统计余额。",
                inclusion_predicates_json=[],
                exclusion_predicates_json=[],
                branch_cases_json=[],
                calculation_kind="AGGREGATE",
                calculation_summary="汇总本金余额。",
                dedup_grain_hint="按贷款编号与分类等级防重汇总",
                internal_locator_json={"regime": "EAST"},
            ),
            LogicEvidenceModel(
                id=str(uuid.uuid4()),
                material_id=m_code.id,
                logic_fingerprint="fp-east-paraphrase-2",
                business_domain_hint="对公贷款五级分类统计（监管报送口径）",
                target_concept_hint="不良贷款余额二期",
                source_summary="补充汇总同一口径。",
                inclusion_predicates_json=[],
                exclusion_predicates_json=[],
                branch_cases_json=[],
                calculation_kind="DIRECT",
                calculation_summary="汇总本金余额。",
                dedup_grain_hint="按贷款编号与分类等级防重汇总",
                internal_locator_json={"regime": "EAST"},
            ),
            LogicEvidenceModel(
                id=str(uuid.uuid4()),
                material_id=m_code.id,
                logic_fingerprint="fp-safe-paraphrase",
                business_domain_hint="国际收支涉外收入申报（涉外收入（境外汇入汇款）业务）",
                target_concept_hint="涉外收入免申报筛选",
                source_summary="机构全量或超五千美元纳入。",
                inclusion_predicates_json=[],
                exclusion_predicates_json=[],
                branch_cases_json=[],
                calculation_kind="DIRECT",
                calculation_summary="逐笔传出。",
                dedup_grain_hint="",
                internal_locator_json={"regime": "SAFE"},
            ),
            LogicEvidenceModel(
                id=str(uuid.uuid4()),
                material_id=m_code.id,
                logic_fingerprint="fp-sac-unreported",
                business_domain_hint="应报未报漏报风险识别",
                target_concept_hint="应报未报风险清单",
                source_summary="单独识别应报未报账户。",
                inclusion_predicates_json=[],
                exclusion_predicates_json=[],
                branch_cases_json=[],
                calculation_kind="DIRECT",
                calculation_summary="逐笔统计。",
                dedup_grain_hint="",
                internal_locator_json={"regime": "SAFE"},
            ),
        ]
    )
    session.flush()

    summary = synthesize_business_assets(session, settings=settings)
    assert summary["capability_count"] == 4
    pano = get_asset_panorama(session)
    caps = {c["name"]: c for c in pano["capabilities"]}
    assert caps["EAST5.0 对公贷款五级分类统计"]["coverage_status"] == "COVERED"
    assert caps["SAFE FAL 国际收支涉外收入申报"]["coverage_status"] == "COVERED"
    assert caps["应报未报漏报风险识别"]["coverage_status"] == "COVERED"
    assert caps["大额与可疑外汇交易报送"]["coverage_status"] == "UNREALIZED"
    assert "对公贷款五级分类统计（监管报送口径）" not in caps
    assert "国际收支涉外收入申报（涉外收入（境外汇入汇款）业务）" not in caps
    east_dedups = [
        r for r in pano["rules"]
        if r["capability_name"] == "EAST5.0 对公贷款五级分类统计" and r["rule_category"] == "DEDUP"
    ]
    assert len(east_dedups) == 1


def test_dual_taxonomy_subdomains_subject_themes_migration_batching_and_cross_report_copilot(tmp_path):
    """验证金融十大主题 + 二级子专题、旧库幂等加列升级、Stage3 分批全子专题精炼、API 过滤及 Copilot 横切问答。"""
    import uuid
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api.common import db_session
    from app.copilot.business_tools import BUSINESS_TOOL_SCHEMAS
    from app.infra.db import Base, ensure_schema_columns
    from app.infra.db_tables import (
        BusinessCapabilityModel,
        BusinessRuleAssetModel,
        LogicEvidenceModel,
        SourceMaterialModel,
    )
    from app.infra.taxonomy import (
        FINANCIAL_SUBJECT_THEMES,
        detect_query_financial_themes,
        infer_financial_theme,
        infer_sub_domain,
    )
    from app.main import create_app

    # 1) Layer 0 taxonomy 纯中文零黑话、覆盖全部 10 大金融主题与多类子专题
    assert len(FINANCIAL_SUBJECT_THEMES) == 10
    assert detect_technical_jargon(" ".join(FINANCIAL_SUBJECT_THEMES)) == []
    assert infer_financial_theme("质押定期存单扣减净额", capability_name="1104 G01 同业存放与拆放核算") == "授信与押品"
    assert infer_financial_theme("仅纳入审核通过的人工补录记录", capability_name="PLUS 手工补录与人工覆盖规范") == "渠道与媒介"
    assert "授信与押品" in detect_query_financial_themes("在所有监管报表里，涉及“授信与押品”和“风控与合规管控”主题怎么算？")
    assert "风控与合规管控" in detect_query_financial_themes("涉及授信与押品或风控与合规管控主题")

    sd_buy = infer_sub_domain("机构大额结售汇及资金来源与使用报送", "购汇资金来源映射", rule_category="INCLUSION")
    sd_use = infer_sub_domain("机构大额结售汇及资金来源与使用报送", "结汇资金使用去向", rule_category="INCLUSION")
    sd_g01_net = infer_sub_domain("1104 G01 同业存放与拆放核算", "单笔净额以账面余额扣减质押金额确定", rule_category="INCLUSION")
    assert sd_buy != sd_use
    assert sd_g01_net == "质押扣减与净额核算"
    assert detect_technical_jargon(f"{sd_buy} {sd_use} {sd_g01_net}") == []

    # 2) 验证已有旧表（缺失 sub_domain / subject_theme 列）可被 ensure_schema_columns 幂等无损迁移
    legacy_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with legacy_engine.begin() as conn:
        conn.execute(text("CREATE TABLE business_rule_assets (id VARCHAR(36) PRIMARY KEY, code VARCHAR(96), title VARCHAR(255))"))
        conn.execute(text("CREATE TABLE business_caliber_assets (id VARCHAR(36) PRIMARY KEY, code VARCHAR(96), metric_name VARCHAR(255))"))
        conn.execute(text("INSERT INTO business_rule_assets (id, code, title) VALUES ('r1', 'rule-old-1', '旧库保留规则')"))
    ensure_schema_columns(legacy_engine)
    ensure_schema_columns(legacy_engine)
    cols_rule = {c["name"] for c in inspect(legacy_engine).get_columns("business_rule_assets")}
    cols_cal = {c["name"] for c in inspect(legacy_engine).get_columns("business_caliber_assets")}
    assert {"sub_domain", "subject_theme"}.issubset(cols_rule)
    assert {"sub_domain", "subject_theme"}.issubset(cols_cal)
    with legacy_engine.connect() as conn:
        assert conn.execute(text("SELECT title FROM business_rule_assets WHERE id = 'r1'")).scalar() == "旧库保留规则"

    # 3) 验证大域（>20 条规则且跨多个二级子专题）触发按子专题分批送入 Stage 3 AI，突破原 18 条截断
    batch_session = make_session()
    settings = Settings(blob_dir=str(tmp_path / "blobs"))
    m_big = SourceMaterialModel(
        id=str(uuid.uuid4()),
        title="big_jsh.sql",
        material_kind="CODE_LOGIC",
        file_type="sql",
        sha256="hash-big-jsh",
        blob_uri="local://big",
        item_count=1,
        summary="big",
    )
    batch_session.add(m_big)
    batch_session.flush()
    preds_buy = [
        {"rule_code": f"BUY_{i}", "title": f"购汇资金来源校验-{i}", "condition": f"购汇用途分类代码第{i}项有效", "rationale": "核验购汇资金来源。"}
        for i in range(15)
    ]
    preds_use = [
        {"rule_code": f"USE_{i}", "title": f"结汇资金使用核验-{i}", "condition": f"结汇资金去向分类代码第{i}项合规", "rationale": "核验结汇资金使用。"}
        for i in range(15)
    ]
    preds_opp = [
        {"rule_code": f"OPP_{i}", "title": f"交易对手总账映射-{i}", "condition": f"交易对手命中内部总账科目第{i}类", "rationale": "核对对手映射。"}
        for i in range(10)
    ]
    batch_session.add(
        LogicEvidenceModel(
            id=str(uuid.uuid4()),
            material_id=m_big.id,
            logic_fingerprint="fp-big-jsh",
            business_domain_hint="机构大额结售汇及资金来源与使用报送",
            target_concept_hint="大额结售汇全流程",
            source_summary="涵盖购汇资金来源、结汇资金使用与对手映射 40 条规则。",
            inclusion_predicates_json=preds_buy + preds_use + preds_opp,
            exclusion_predicates_json=[],
            branch_cases_json=[],
            calculation_kind="DIRECT",
            calculation_summary="按实际申报原值传递。",
            dedup_grain_hint="",
            internal_locator_json={"regime": "SAFE"},
        )
    )
    batch_session.flush()

    class _BatchInspectLlm:
        model = "deepseek-flash"

        def __init__(self) -> None:
            self.batches = []
            self.refined_codes = set()

        def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
            if tool_name == "synthesize_domain_assets":
                self.batches.append((payload.get("batch_index"), payload.get("sub_domain_focus"), len(payload.get("draft_rules") or [])))
                out_rules = []
                for r in payload.get("draft_rules") or []:
                    self.refined_codes.add(r["code"])
                    out_rules.append(
                        {
                            "code": r["code"],
                            "title": r["title"],
                            "rule_category": r["rule_category"],
                            "sub_domain": r["sub_domain"],
                            "subject_theme": r["subject_theme"],
                            "business_condition": f"【批量精炼】{r['business_condition']}",
                            "business_action": r["business_action"],
                            "business_rationale": r["business_rationale"],
                            "evidence_ids": r.get("evidence_ids") or [],
                        }
                    )
                return {
                    "description": "完成全子专题融合",
                    "scope_summary": "涵盖多子专题",
                    "rules": out_rules,
                    "calibers": [],
                }, {"provider": "test", "model": "deepseek-flash"}
            return {}, {"provider": "test", "model": "deepseek-flash"}

    batch_llm = _BatchInspectLlm()
    batch_summary = synthesize_business_assets(batch_session, settings=settings, llm=batch_llm)
    assert batch_summary["rule_count"] == 40
    assert len(batch_llm.batches) >= 2
    assert len(batch_llm.refined_codes) == 40
    refined_rules_db = batch_session.query(BusinessRuleAssetModel).all()
    assert all(r.business_condition.startswith("【批量精炼】") for r in refined_rules_db)
    assert len({r.sub_domain for r in refined_rules_db}) == 3

    # 4) 验证四体系 FastAPI 全景 / taxonomy 过滤 + Copilot 横切十大主题检索（保持 7 工具不变）
    assert len(BUSINESS_TOOL_SCHEMAS) == 7
    api_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=api_engine)
    ApiSession = sessionmaker(bind=api_engine, autoflush=False, expire_on_commit=False)
    s_api = ApiSession()
    for kind, ftype, title, raw in [
        (
            "REGULATORY_DOC",
            "md",
            "1104_G01_同业存放规范.md",
            "# 1104 G01 同业存放与拆放核算\n\n## 同业存放净额计算口径\n按账面余额扣减已质押定期存单后的净额填报，剔除测试户。".encode("utf-8"),
        ),
        (
            "CODE_LOGIC",
            "sql",
            "proc_1104_g01.sql",
            "/* 报表名称: 1104 G01 同业存放与拆放核算 */\nINSERT INTO RPT_1104_G01 SELECT CUST_TYPE, SUM(BOOK_BAL - PLEDGE_AMT) FROM CORE_IB WHERE STATUS <> 'TEST' GROUP BY CUST_TYPE;".encode("utf-8"),
        ),
        (
            "REGULATORY_DOC",
            "md",
            "EAST_不良贷款.md",
            "# EAST5.0 对公贷款五级分类统计\n\n## 不良与核销剔除\n统计次级可疑损失不良贷款，剔除已核销记录。".encode("utf-8"),
        ),
        (
            "CODE_LOGIC",
            "sql",
            "proc_east_loan.sql",
            "/* 业务说明: EAST5.0 对公贷款五级分类统计 */\nINSERT INTO EAST_LOAN SELECT LOAN_ID, SUM(PRINCIPAL_BAL) FROM CORE_LOAN WHERE GRADE_CODE IN ('3','4','5') AND WRITE_OFF_FLAG <> 'Y' GROUP BY LOAN_ID;".encode("utf-8"),
        ),
    ]:
        ingest_material(s_api, title=title, material_kind=kind, file_type=ftype, content=raw, settings=settings)
    s_api.commit()
    synthesize_business_assets(s_api, settings=settings)
    s_api.commit()

    def _override():
        db = ApiSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[db_session] = _override
    with TestClient(app) as client:
        p_resp = client.get("/api/assets/panorama")
        assert p_resp.status_code == 200
        pano_api = p_resp.json()
        assert len(pano_api["taxonomy"]["subject_themes"]) == 10
        assert len(pano_api["taxonomy"]["regimes"]) >= 2
        assert all(c.get("sub_domains") and c.get("subject_themes") for c in pano_api["capabilities"])
        assert detect_technical_jargon(json.dumps({
            "capabilities": pano_api["capabilities"],
            "rules": pano_api["rules"],
            "calibers": pano_api["calibers"],
            "reconcile": pano_api["reconcile"],
        }, ensure_ascii=False)) == []

        f_resp = client.get("/api/assets/rules", params={"subject_theme": "授信与押品"})
        assert f_resp.status_code == 200
        credit_rules = f_resp.json()
        assert len(credit_rules) >= 1
        assert all(r["subject_theme"] == "授信与押品" for r in credit_rules)

    cross_res = execute_business_tool(s_api, "get_business_scope_and_rules", {"topic": "在所有监管报表里，涉及「授信与押品」主题的规则有哪些？"})
    assert cross_res["rules"]
    assert all(r["subject_theme"] == "授信与押品" for r in cross_res["rules"])
    cross_qa = ask_business_copilot(s_api, "在所有监管报表里，涉及「授信与押品」主题的剔除与扣减口径有哪些？", settings=settings)
    assert cross_qa["verification"]["de_jargon_passed"] is True
    assert cross_qa["verification"]["residual_jargon"] == []
