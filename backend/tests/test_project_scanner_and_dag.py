"""Phase 0 零 LLM 静态预扫描、模板聚簇、跨脚本血缘 DAG 与 Stage 2 精简/上下文透传测试。"""

import json

from app.ingestion.code_parser import parse_code_logic
from app.ingestion.project_scanner import (
    TIER_1_CORE,
    TIER_2_UPSTREAM,
    TIER_3_SKIP_AI,
    scan_project_batch,
)
from app.policy.dc_bus import default_bus, detect_technical_jargon


def test_project_scanner_tiers_backups_ddl_ods_and_30_branch_clones():
    scripts = [
        ("m-bak", "rpt_g01_loan_bak.sql", "bak/rpt_g01_loan_bak.sql", "/* 1104 G01 */ INSERT INTO RPT_G01_LOAN SELECT * FROM DWD_LOAN WHERE BAL > 0;"),
        ("m-old", "dwd_loan_20250101.sql", "dwd/dwd_loan_20250101.sql", "INSERT INTO DWD_LOAN SELECT * FROM ODS_LOAN WHERE STATUS='1';"),
        ("m-ddl", "init_tables.sql", "ddl/init_tables.sql", "CREATE TABLE DWD_LOAN (ACCT_NO VARCHAR2(32), BAL NUMBER); CREATE INDEX IDX_1 ON DWD_LOAN(ACCT_NO);"),
        ("m-log", "sp_log_cleanup.sql", "ops/sp_log_cleanup.sql", "INSERT INTO ETL_JOB_LOG SELECT * FROM SYS_RUN_TMP WHERE STATE = 'OK';"),
        ("m-ods", "ods_loan_raw.sql", "ods/ods_loan_raw.sql", "INSERT INTO ODS_LOAN_RAW SELECT ACCT_NO, CUST_NO, BAL FROM SRC_CORE_LOAN;"),
        ("m-dwd", "dwd_loan_clean.sql", "dwd/dwd_loan_clean.sql", "/* 清洗对公贷款 */ INSERT INTO DWD_LOAN_CLEAN SELECT CUST_NO, BAL FROM ODS_LOAN_RAW WHERE TEST_FLAG <> 'Y' AND STATUS = 'ACTIVE';"),
        ("m-orphan", "dwd_unused_tmp.sql", "dwd/dwd_unused_tmp.sql", "INSERT INTO TMP_UNUSED_MID SELECT A.* FROM ODS_LOAN_RAW A WHERE A.BAL > 10;"),
    ]
    for i in range(1, 31):
        br = f"br{i:02d}"
        scripts.append(
            (
                f"m-br-{i}",
                f"rpt_1104_g01_{br}.sql",
                f"rpt/1104/rpt_1104_g01_{br}.sql",
                f"""
                /* 1104 G01 资产负债项目统计表分机构汇总 */
                INSERT INTO RPT_1104_G01_{br.upper()}
                SELECT CUST_NO, SUM(BAL) AS TOTAL_BAL
                FROM DWD_LOAN_CLEAN
                WHERE ORG_CD = '{br.upper()}'
                GROUP BY CUST_NO;
                """,
            )
        )

    res = scan_project_batch(scripts, enable_template_dedup=True, max_workers=4)
    by_id = {p.material_id: p for p in res["profiles"]}

    assert by_id["m-bak"].tier == TIER_3_SKIP_AI and by_id["m-bak"].skip_reason == "BACKUP_FILE"
    assert by_id["m-old"].tier == TIER_3_SKIP_AI and by_id["m-old"].skip_reason == "BACKUP_FILE"
    assert by_id["m-ddl"].tier == TIER_3_SKIP_AI and by_id["m-ddl"].skip_reason == "PURE_DDL"
    assert by_id["m-log"].tier == TIER_3_SKIP_AI and by_id["m-log"].skip_reason == "HOUSEKEEPING_LOG"
    assert by_id["m-ods"].tier == TIER_3_SKIP_AI and by_id["m-ods"].skip_reason == "ODS_PASSTHROUGH"
    assert by_id["m-orphan"].tier == TIER_3_SKIP_AI and by_id["m-orphan"].skip_reason == "ORPHAN_INTERMEDIATE"

    br_tiers = [by_id[f"m-br-{i}"].tier for i in range(1, 31)]
    assert br_tiers.count(TIER_1_CORE) == 1
    assert br_tiers.count(TIER_3_SKIP_AI) == 29
    clones = [by_id[f"m-br-{i}"] for i in range(1, 31) if by_id[f"m-br-{i}"].skip_reason == "TEMPLATE_CLONE"]
    assert len(clones) == 29
    master_id = next(by_id[f"m-br-{i}"].material_id for i in range(1, 31) if by_id[f"m-br-{i}"].tier == TIER_1_CORE)
    assert all(c.template_master_id == master_id for c in clones)

    assert by_id["m-dwd"].tier == TIER_2_UPSTREAM
    assert by_id["m-dwd"].topo_order < by_id[master_id].topo_order
    assert "m-dwd" in res["upstream_map"][master_id]


def test_lineage_dag_breaks_mutual_cycles_and_lite_upstream_propagation():
    cycle_scripts = [
        ("c1", "dwd_a.sql", "dwd/dwd_a.sql", "/* 环节A */ INSERT INTO DWD_A SELECT * FROM DWD_B WHERE BAL > 0;"),
        ("c2", "dwd_b.sql", "dwd/dwd_b.sql", "/* 环节B */ INSERT INTO DWD_B SELECT * FROM DWD_A WHERE STATUS = '1';"),
        ("r1", "rpt_g04.sql", "rpt/rpt_g04.sql", "/* 1104 G04 利润报表 */ INSERT INTO RPT_G04 SELECT CUST_NO, SUM(AMT) FROM DWD_B GROUP BY CUST_NO;"),
    ]
    res = scan_project_batch(cycle_scripts, enable_template_dedup=True, max_workers=2)
    by_id = {p.material_id: p for p in res["profiles"]}
    assert len(res["profiles"]) == 3
    assert by_id["r1"].tier == TIER_1_CORE
    assert by_id["c1"].tier == TIER_2_UPSTREAM or by_id["c2"].tier == TIER_2_UPSTREAM

    class _SpyLlm:
        def __init__(self):
            self.calls = []

        def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
            self.calls.append((tool_name, payload))
            if tool_name == "plan_code_structure":
                steps = payload["steps"]
                return (
                    {
                        "business_overview": "个人存款两阶段归集与汇总统计",
                        "canonical_domain": "个人存款监管汇总报送",
                        "chunk_groups": [
                            {
                                "stage_title": "汇总与核验输出阶段",
                                "business_domain": "个人存款监管汇总报送",
                                "stage_role": "CALCULATION",
                                "step_indices": [s["step_index"] for s in steps],
                                "stage_purpose": "合并全量个人存款并完成净额计算",
                            }
                        ],
                    },
                    {"usage": {"total_tokens": 50}},
                )
            return (
                {
                    "business_domain_hint": payload.get("business_domain_hint") or "个人存款监管汇总报送",
                    "target_concept_hint": "个人存款净额指标",
                    "source_summary": "汇总有效存续客户个人存款金额，继承上游对测试户与演练户的前置剔除结果。",
                    "calculation_kind": "AGGREGATE",
                    "calculation_summary": "剔除质押冻结金额后汇总客户个人有效净额。",
                    "inclusions": [
                        {
                            "rule_code": "VALID_DEPOSIT_ONLY",
                            "title": "纳入正常存续个人存款并扣减冻结部分",
                            "condition": "沿用上游已剔除测试账户后的有效客户，汇总净额",
                            "rationale": "保持跨阶段端到端统计口径一致",
                        }
                    ],
                    "exclusions": [],
                    "branches": [],
                    "dedup_grain_hint": "按客户汇总唯一净额。",
                },
                {"usage": {"total_tokens": 80}},
            )

    llm = _SpyLlm()
    bus = default_bus()

    multi_step_dwd = """
    INSERT INTO TMP_DWD_1 SELECT CUST_NO, AMT FROM ODS_DEP WHERE TEST_FLG <> 'Y';
    INSERT INTO DWD_DEP_CLEAN SELECT CUST_NO, AMT FROM TMP_DWD_1 WHERE AMT > 0;
    """.encode("utf-8")
    t2_evs = parse_code_logic("dwd_dep.sql", multi_step_dwd, llm=llm, bus=bus, execution_mode="lite")
    assert len(llm.calls) == 1
    assert llm.calls[0][0] == "translate_logic_evidence"
    assert all((e.internal_locator or {}).get("plan_stage") == "TIER2_LITE" for e in t2_evs)

    llm.calls.clear()
    multi_step_rpt = """
    /* 1104 G01 个人有效存款 */
    INSERT INTO TMP_RPT_1 SELECT CUST_NO, SUM(AMT - PLEDGE_AMT) AS NET_BAL FROM DWD_DEP_CLEAN GROUP BY CUST_NO;
    INSERT INTO RPT_G01_DEP SELECT CUST_NO, NET_BAL FROM TMP_RPT_1 WHERE NET_BAL > 0;
    """.encode("utf-8")
    up_ctx = [
        {
            "stage_topic": "上游个人存款清洗",
            "business_summary": "已在前置加工环节剔除测试客户及余额为零的账户。",
            "key_rules": ["自动剔除内部测试标志记录与零余额账户", "涉及表 ODS_DEP 与 TEST_FLG"],
        }
    ]
    t1_evs = parse_code_logic(
        "rpt_g01.sql",
        multi_step_rpt,
        llm=llm,
        bus=bus,
        execution_mode="full",
        upstream_context=up_ctx,
    )
    assert llm.calls[0][0] == "plan_code_structure"
    plan_json = json.dumps(llm.calls[0][1], ensure_ascii=False)
    assert "upstream_business_context" not in plan_json
    assert "ODS_DEP" not in plan_json

    extract_call = next(p for name, p in llm.calls if name == "translate_logic_evidence")
    assert "upstream_business_context" in extract_call
    extract_ctx_json = json.dumps(extract_call["upstream_business_context"], ensure_ascii=False)
    assert detect_technical_jargon(extract_ctx_json) == []
    assert "ODS_DEP" not in extract_ctx_json
    assert t1_evs
