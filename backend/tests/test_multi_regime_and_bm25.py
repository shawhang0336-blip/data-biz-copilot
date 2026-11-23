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
