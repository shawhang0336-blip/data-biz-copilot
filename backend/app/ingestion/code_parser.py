"""SQL / 存储过程代码逻辑静默解析器（接入层 Layer 3）。

借鉴 `sql_explorer/engine` 的切块与确定性语义提炼思路：
- 按语句、PL/SQL 块及 `EXECUTE IMMEDIATE` 动态 SQL 边界切分代码；
- 支持 Oracle SAC 存储过程头注释提炼与 `SAC_TABLE_SCHEMAS` 建表结构按域聚合；
- 确定性提取业务过滤（纳入范围）、剔除条件（风险隔离/测试过滤）、分类分支、
  防重粒度（ROW_NUMBER / MERGE / 重跑删除）与指标算式形态；
- 原始物理表名、字段名与脚本行号严格封存在 `internal_locator` 中，绝不越层泄漏。
"""

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from app.infra.contracts import ExtractedLogicEvidence
from app.infra.retrieval import infer_regulatory_regime
from app.ingestion.ast_extractor import (
    enrich_script_evidences_with_llm,
    extract_sql_ast_skeleton,
    plan_and_extract_script_evidences,
    synthesize_generic_business_semantics,
    translate_evidence_with_llm,
)


def _unwrap_execute_immediate(sql_text: str) -> str:
    """将 PL/SQL 中的 `EXECUTE IMMEDIATE '...'` 动态 SQL 展开为一等 SQL 语句以便提取逻辑。"""
    def _repl(match: re.Match) -> str:
        inner = match.group(1).replace("''", "'")
        return f"\n{inner};\n"

    return re.sub(
        r"EXECUTE\s+IMMEDIATE\s+'((?:[^']|'')*)'\s*;",
        _repl,
        sql_text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def split_sql_chunks(sql_text: str) -> List[Tuple[int, str]]:
    """尊重字符串常量与注释块的确定性 SQL 语句切块（自动解包动态 SQL）。"""
    unwrapped = _unwrap_execute_immediate(sql_text)
    chunks: List[Tuple[int, str]] = []
    buf: List[str] = []
    in_single = False
    in_line_comment = False
    in_block_comment = False
    start_line = 1
    line_no = 1
    i = 0
    n = len(unwrapped)
    while i < n:
        ch = unwrapped[i]
        nxt = unwrapped[i + 1] if i + 1 < n else ""
        if ch == "\n":
            in_line_comment = False
            line_no += 1
        if in_line_comment:
            buf.append(ch)
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append("/")
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue
        if not in_single and ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append("--")
            i += 2
            continue
        if not in_single and ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append("/*")
            i += 2
            continue
        if ch == "'":
            in_single = not in_single
            buf.append(ch)
            i += 1
            continue
        if not in_single and ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                chunks.append((start_line, stmt))
            buf = []
            start_line = line_no
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        chunks.append((start_line, tail))
    return chunks


def _extract_comments(chunk: str) -> List[str]:
    comments: List[str] = []
    for m in re.finditer(r"--\s*([^\n]+)", chunk):
        txt = m.group(1).strip()
        if txt and not set(txt).issubset({"-", "=", "*", " "}):
            comments.append(txt)
    for m in re.finditer(r"/\*(.*?)\*/", chunk, flags=re.DOTALL):
        for line in m.group(1).splitlines():
            cleaned = line.strip(" *\t#")
            if cleaned and not set(cleaned).issubset({"-", "=", "*", "#"}):
                comments.append(cleaned)
    return comments


def _extract_procedure_banner(sql_text: str) -> str:
    """从存储过程/SQL 头部注释中提炼中文任务/报表/业务描述，作为无行内注释块的基础语义。"""
    head = "\n".join(sql_text.splitlines()[:45])
    comments = _extract_comments(head)
    parts: List[str] = []
    for c in comments:
        for pat in (r"(?:任务描述|程序描述|程序名称|业务说明|功能说明|报表名称|主题|指标说明)[：:\s]*(.+)",):
            m = re.search(pat, c)
            if m and m.group(1).strip():
                parts.append(m.group(1).strip())
    if parts:
        return "；".join(dict.fromkeys(parts))
    return ""


def _extract_physical_targets(chunk: str) -> List[str]:
    targets: List[str] = []
    for pat in (
        r"\b(?:INSERT\s+INTO|MERGE\s+INTO|UPDATE|CREATE\s+TABLE|DELETE\s+FROM)\s+\"?([A-Za-z0-9_$.\"]+)\"?",
        r"\b(?:FROM|JOIN)\s+\"?([A-Za-z0-9_$.\"]+)\"?",
    ):
        for m in re.finditer(pat, chunk, flags=re.IGNORECASE):
            name = m.group(1).replace('"', "").strip().upper()
            if name and name not in targets and name not in {"SELECT", "DUAL", "SET"}:
                targets.append(name)
    return targets


def _is_housekeeping_chunk(stmt: str, targets: List[str]) -> bool:
    """过滤纯粹的调度日志表与异常日志表写入语句，避免稀释业务实证。"""
    upper = stmt.upper()
    if targets and all(
        "SP_LOG" in t or "EXCEPTION_LOG" in t or "ETL_LOG" in t or "LOG_" in t
        for t in targets
    ):
        return True
    if any(k in upper for k in ("INSERT INTO SP_LOG", "INSERT INTO MAIN_EXCEPTION_LOG", "UPDATE SP_LOG")):
        return True
    if any(k in upper for k in ("TRUNCATE TABLE", "ALTER SESSION", "COMMIT", "ANALYZE TABLE", "DBMS_STATS")):
        if not any(op in upper for op in ("INSERT ", "MERGE ", "SELECT ")):
            return True
    if re.match(r"^(?:BEGIN|END|EXCEPTION|WHEN\s+OTHERS|DECLARE|SET\s+DEFINE)\b", upper.strip()):
        if not any(op in upper for op in ("INSERT ", "MERGE ", "SELECT ")):
            return True
    return False


_SAC_CUES = (
    "SAC_", "SF_SAC_", "SP_SAC_", "GRCKFHZ", "DGKHXXB", "JGXXB", "SXXXB", "JGDEJSH", "LARGEFUNDS",
    "UNREPORT", "应报未报", "漏报", "DFVLOC", "DEALAMOUNT", "ACCOUNTBALAN", "KHHZH",
    "DDACMSP", "HSMSMP", "GLACMSP", "分户账", "个人存款分户账", "联名账户", "跨境联名账户",
    "JOINT_BAL", "大额与可疑外汇", "TDACMSP", "IPACMSP", "EPACMSP",
)


def _is_known_sac_logic(filename: str, banner: str, comments: List[str], targets: List[str], chunk: str) -> bool:
    blob = " ".join([filename, banner, *comments, *targets, chunk]).upper()
    return any(cue in blob for cue in _SAC_CUES)


def _infer_domain_and_concept(
    filename: str, chunk: str, comments: List[str], targets: List[str], banner: str = ""
) -> Tuple[str, str]:
    fn_upper = filename.upper()
    blob = " ".join([filename, banner, *comments, *targets, chunk]).upper()

    # 1) 优先按精确目标表 / 专属存储过程判定（保证多业务并行脚本精确归类）
    if "UNREPORT" in blob or "应报未报" in blob or "漏报" in blob or "DFVLOC IS NULL" in blob:
        if "INSERT INTO SAC_UNREPORT" in blob or "FROM SAC_TMP_DDACMSP" in blob or "应报未报漏报风险账户" in blob:
            return "应报未报漏报风险识别", "应报未报漏报风险账户清单"

    if "SP_SAC_ACCOUNT_INFO" in fn_upper or "SP_SAC_ACCT_AMOUNT" in fn_upper:
        if "DEALAMOUNT" in blob or "ACCOUNTBALAN" in blob:
            return "大额与可疑外汇交易报送", "大额与可疑交易报送核心指标"
        return "大额与可疑外汇交易报送", "大额与可疑外汇交易报送圈定与分支"

    if any(k in fn_upper for k in ("EAST_GRCKFHZ", "GRCKFHZMX", "GRCKFHZ", "GET_BDM_DATA", "GET_EAST_DATA")) or (
        ("GRCKFHZ" in blob or "个人存款分户账" in blob) and "SAC_LARGEFUNDS" not in blob and "SAC_JGDEJSH" not in blob
    ):
        return "个人存款分户账明细加工", "个人存款分户账及明细监管报送"

    if any(k in fn_upper for k in ("EAST_DGKHXXB", "TMP_CUST_ID")) or any(
        k in blob for k in ("EAST_ODS_DGKHXXB", "DGKHXXB_", "BDM_CUS_CCUSTOMER", "ONSHORE_FLG", "捞回标记", "TAG_LH")
    ):
        return "对公客户信息与境内外身份监管报送", "对公客户信息与境内外身份报送"

    if "EAST_JGXXB" in fn_upper or "EAST_ODS_JGXXB" in blob or ("机构信息表" in blob and "BDM_PUB_BRANCH" in blob):
        return "银行机构信息与层级架构监管报送", "银行机构信息与层级架构报送"

    if "EAST_SXXXB" in fn_upper or "EAST_ODS_SXXXB" in blob or "BDM_SXXXB_TMP" in blob or "授信信息表" in blob:
        return "客户综合授信额度信息监管报送", "客户综合授信额度信息报送"

    if any(k in fn_upper for k in ("JGDEJSH", "LARGEFUNDS", "LARGE_BULU", "LARGE_SRC_PSV", "LARGE_USE_PSV")) or any(
        k in blob
        for k in (
            "SAC_JGDEJSH",
            "SF_SAC_LARGEFUNDS",
            "LARGE_SRC_MAPPING",
            "LARGE_USE_MAPPING",
            "机构大额结汇",
            "机构大额购汇",
            "机构大额结售汇",
            "变动编号",
        )
    ):
        return "机构大额结售汇及资金来源与使用报送", "机构大额结售汇资金来源与使用"

    if "联名账户" in blob or "JOINT_BAL" in blob:
        return "大额与可疑外汇交易报送", "跨境联名账户折算余额"
    if "DEALAMOUNT" in blob or "ACCOUNTBALAN" in blob:
        return "大额与可疑外汇交易报送", "大额与可疑交易报送核心指标"
    return "大额与可疑外汇交易报送", "大额与可疑外汇交易报送圈定与分支"


def _add_unique_rule(bucket: List[Dict[str, Any]], item: Dict[str, Any]) -> None:
    if not any(existing["rule_code"] == item["rule_code"] for existing in bucket):
        bucket.append(item)


def _translate_predicates(
    domain: str, chunk: str, comments: List[str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    upper = chunk.upper()
    joined_comments = " ".join(comments)
    inclusions: List[Dict[str, Any]] = []
    exclusions: List[Dict[str, Any]] = []

    # 通用申报窗口与重点名单条件（涉外外汇大额可疑等）
    if any(k in upper for k in ("START_DATE", "END_DATE", "APPLYSTARTDATE", "APPLYENDDATE", "DEAL_DATE BETWEEN", "DEALDATE BETWEEN", "TRANS_DATE BETWEEN", "申报区间")):
        _add_unique_rule(
            inclusions,
            {
                "rule_code": "INC_REPORTING_WINDOW",
                "title": "申报统计区间范围限定",
                "condition": "交易或余额记录的发生日期处于当期设定的监管申报开始至结束日期区间内",
                "rationale": "确保只提取当期合规申报时间窗口内发生的有效业务数据，避免跨期混入。",
            },
        )
    if "KHHZH_LIST" in upper or "客户清单" in joined_comments or "重点监控" in joined_comments:
        _add_unique_rule(
            inclusions,
            {
                "rule_code": "INC_MONITORED_CUSTOMER_LIST",
                "title": "纳入重点申报客户与账户清单范围",
                "condition": "交易主体必须属于当期已识别纳入监管报送清单的客户及关联账户",
                "rationale": "仅对经核定列入报送主体范围的客户及其有效账户生成报送明细，无关客户不进入申报。",
            },
        )
    if "CURRENCY" in upper or "DEALCURR" in upper or "ACCT_CURR" in upper or "'ALL'" in upper or "币种" in joined_comments:
        if any(k in upper for k in ("DEALCURR", "ACCT_CURR", "CURRENCY", "'ALL'")):
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_CURRENCY_SCOPE",
                    "title": "币种筛选与全币种统括处理",
                    "condition": "按业务申报指定的交易币种精确匹配；当客户清单设定为全币种申报时，则纳入该客户下所有币种交易",
                    "rationale": "兼顾单币种专项核查与全币种统括客户的完整报送要求。",
                },
            )
    if "SAC_TXN_COUNTER" in upper and "SAC_TXN_ONLINE" in upper:
        _add_unique_rule(
            inclusions,
            {
                "rule_code": "INC_DUAL_CHANNEL_TXN_RANK",
                "title": "柜面与网银双渠道交易汇总及机构排名",
                "condition": "合并纳入柜面与网上银行双渠道当日交易流水，按机构与交易金额降序汇总监测",
                "rationale": "统一多渠道大额交易监测视图，防范同一机构内跨渠道大额交易遗漏。",
            },
        )
    if "港澳通行证" in joined_comments or "CERTTYPE = '06'" in upper or "LENGTH(T.ENCODE) = 9" in upper:
        _add_unique_rule(
            inclusions,
            {
                "rule_code": "INC_THREE_WAY_IDENTITY_MATCH",
                "title": "卡号、账号与证件号（含港澳通行证）三路归集匹配",
                "condition": "当核查申请按卡号、剔除特定前缀的核心账号或客户证件号发起时，自动三路联动匹配并兼容 9 位与 11 位港澳通行证号及 9 位对公组织机构证件",
                "rationale": "解决监管查询函仅提供卡号或部分证件位数导致的客户账户漏查问题。",
            },
        )
    if "99991231" in upper and ("CLOSEDATE" in upper or "XHRQ" in upper or "销户" in joined_comments):
        _add_unique_rule(
            inclusions,
            {
                "rule_code": "INC_OPEN_ACCOUNT_DEFAULT_DATE",
                "title": "未销户账户日期标识转换（长期有效转未销户态）",
                "condition": "对于登记为长期有效占位日期但实际仍处于正常存续状态的未销户账户，在生成报送基础信息时按未销户状态处理",
                "rationale": "确保基础账户要素与监管报送标准对未销户状态的格式要求保持一致。",
            },
        )

    # 应报未报与剔除规则
    if "UNREPORT" in upper or "DFVLOC IS NULL" in upper or "应报未报" in joined_comments:
        if "NOT EXISTS" in upper:
            _add_unique_rule(
                exclusions,
                {
                    "rule_code": "EXC_UNREPORTED_MISSING_MAPPING",
                    "title": "自动剔除缺少报送属地映射的应报未报待补报风险账户",
                    "condition": "若客户交易已发生但账户尚缺报送属地映射信息（应报未报账户），则从当期常规报送明细中自动剔除，转入漏报风险待补报清单",
                    "rationale": "这是风险隔离与合规补报口径——并非交易丢失，而是避免残缺属地数据直接报送被拒收，先交由合规与业务补全映射后补报。",
                },
            )
        else:
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_UNREPORTED_RISK_IDENTIFY",
                    "title": "已交易但缺失报送属地映射的漏报风险识别",
                    "condition": "识别出在申报期内已实际发生交易、但账户缺少监管报送属地标识的账户并单独登记",
                    "rationale": "将待补报风险敞口提前暴露给业务与合规人员跟踪补录，防范监管漏报风险。",
                },
            )
    if "REPORTED_FLAG <> 'Y'" in upper or "已报送" in joined_comments or "CANCEL_FLAG" in upper or "NOT EXISTS (SELECT 1 FROM SF_SAC_BASE_ACCOUNT_INFO" in upper:
        _add_unique_rule(
            exclusions,
            {
                "rule_code": "EXC_ALREADY_REPORTED_OR_CANCELLED",
                "title": "剔除当期已报送或已冲正撤回交易",
                "condition": "排除已经完成申报确认的重复记录以及业务上已作废撤回的冲正流水",
                "rationale": "防止同一笔业务重复申报或将已撤销交易误报给监管机构。",
            },
        )

    # 个人存款分户账及明细领域规则
    if domain == "个人存款分户账明细加工":
        if "ACCT_STATUS" in upper or "VALID_FLAG" in upper or "BDM_ACC_DEPOSIT_ACCT" in upper or "有效账户" in joined_comments:
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_ACTIVE_ACCOUNT_EVENT",
                    "title": "有效存款交易与账户及个人客户信息关联归并",
                    "condition": "关联存款账户信息、个人客户信息及业务办理机构（含联名存款客户按主客户号归并），保留有效存续及当月新销户存款分户账明细",
                    "rationale": "确保个人存款分户账及明细具备完整的账户归属与客户身份要素，满足 EAST 报送与账务核对需要。",
                },
            )
        if "F.ID_TYPE LIKE 'C%'" in upper or "个体工商户" in joined_comments:
            _add_unique_rule(
                exclusions,
                {
                    "rule_code": "EXC_CORP_CERT_INDIVIDUAL_BIZ",
                    "title": "自动剔除以对公证件开户的个体工商户账户",
                    "condition": "当个人存款关联客户在证件体系中存在对公机构类证件开户记录时，从个人存款分户账报送中自动剔除",
                    "rationale": "遵循 EAST 报送分类规范，以机构名义登记资料的个体工商户归入对公维度管理，避免个人与对公口径交叉重复。",
                },
            )
        if "迁移资产" in upper or "20240611" in upper:
            _add_unique_rule(
                exclusions,
                {
                    "rule_code": "EXC_LEGACY_MIGRATED_CLOSED",
                    "title": "剔除历史迁移前已销户的遗留账户及测试账户",
                    "condition": "自动排除早于业务迁移基准日前已销户的历史迁移资产账户，以及带测试删除标记的虚拟账户",
                    "rationale": "确保个人存款分户账报送聚焦本行现行真实存续及当期有效变动的个人账户，防止历史失效迁移户污染报送数据。",
                },
            )
        if "E.TAR_NAME<>'销户'" in upper or "CLOSE_DT" in upper:
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_CURRENT_WINDOW_CLOSE_ACCT",
                    "title": "仅纳入未销户或当期申报窗口内新销户账户",
                    "condition": "非销户状态账户全量纳入；若账户状态为已销户，仅当其销户日期落入上一工作日至当期申报日区间时才纳入当期报送",
                    "rationale": "精准捕捉当期新发生销户的账户变动信息，同时过滤往期早已被销户的静默户。",
                },
            )

    # 机构大额结售汇及资金来源与使用领域规则
    if domain == "机构大额结售汇及资金来源与使用报送":
        if any(k in upper for k in ("LARGEFUNDS", "JGDEJSH", "PFE4ORG", "SE4ORG", "FUN_ISWORKDAY", "BULU")):
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_ORG_LARGE_FX_DAILY",
                    "title": "机构大额购汇资金来源与结汇资金使用按日采集及补录",
                    "condition": "针对机构大额结汇资金使用与购汇资金来源业务，在每个工作日汇总贸易融资及柜面大额交易线索，并纳入经人工补录确认的大额资金信息",
                    "rationale": "落实国家外汇管理局关于大额结售汇真实性与资金流向穿透监管的日常监测及补录上报要求。",
                },
            )
        if "30" in upper or "30万" in upper or "30W" in upper:
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_30W_LARGE_FX_THRESHOLD",
                    "title": "机构 30 万美元及以上大额结售汇重点监测纳入",
                    "condition": "对达到机构大额结售汇重点统计门槛的交易，核实关联对公存款账户户名、部门标识并纳入大额结售汇专表监测",
                    "rationale": "及时识别并上报单笔或当期大额结售汇交易，支持跨境资金流动及头寸变动监测。",
                },
            )
        if "变动编号" in joined_comments or "RPTNO" in upper:
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_UNIQUE_CHANGE_REPORT_NO",
                    "title": "同机构同交易日唯一变动编号生成",
                    "condition": "按同一申报银行机构与同一交易日期对大额结售汇资金来源与使用记录编制唯一变动编号标识",
                    "rationale": "确保每笔报送的结汇资金使用或购汇资金来源数据在外汇局报送端拥有可追踪、无冲突的业务序号。",
                },
            )
        if "DELETE FROM SF_SAC_LARGEFUNDS" in upper or "TO_SINGLE_BYTE" in upper or "去空格" in joined_comments:
            _add_unique_rule(
                exclusions,
                {
                    "rule_code": "EXC_RERUN_CLEANUP_AND_WHITESPACE",
                    "title": "重跑清理当日旧记录及全半角空格噪音清洗",
                    "condition": "当同日重新触发处理时自动清除当日已生成的大额购汇来源与结汇使用记录，并清洗交易对手户名、币种及机构码中的全半角空格",
                    "rationale": "避免重跑造成大额结售汇数据翻倍，同时消除空格格式瑕疵导致的报送校验退回。",
                },
            )

    # 对公客户信息域规则
    if domain == "对公客户信息与境内外身份监管报送":
        if "捞回" in upper or "TAG_LH" in upper or "SCYY" in upper:
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_CORP_CREDIT_RETRIEVE_BACK",
                    "title": "对公客户存续授信自动“捞回”保留报送",
                    "condition": "对虽已被标记拟删除的对公客户，只要核查其在当日客户授信报送结果中仍存在有效授信关系，则触发“捞回”机制保留报送",
                    "rationale": "保证监管对公客户台账与授信台账之间的客户编号严格勾稽一致，防止有授信无客户信息的监管校验报错。",
                },
            )
        if "ONSHORE_FLG" in upper or "境内" in joined_comments or "居民" in joined_comments:
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_CORP_RESIDENT_NONRESIDENT_ID",
                    "title": "对公客户身份、有效证件、注册地址与境内外居民标识归集",
                    "condition": "归集对公客户统一编号、按优先序选定的唯一主证件、当月或现行有效的注册地址及境内居民/境外非居民标识",
                    "rationale": "满足银行业客户信息监管报送对企业基本要素完整性及跨境涉外属性识别的要求。",
                },
            )
        if "CLOSE_DT IS NULL" in upper or "TAG_LH = '非捞回'" in upper:
            _add_unique_rule(
                exclusions,
                {
                    "rule_code": "EXC_CORP_HISTORY_CLOSED_NON_RETRIEVED",
                    "title": "剔除历史已关户且无存续授信的对公客户",
                    "condition": "对非当期新关户且未触发授信“捞回”的历史已销户对公客户，以及无授信支撑的删除标记客户予以排除",
                    "rationale": "聚焦存续及当期变动对公客户，避免已终止业务关系的历史客户继续占用常规报送名额。",
                },
            )

    # 银行机构信息域规则
    if domain == "银行机构信息与层级架构监管报送":
        _add_unique_rule(
            inclusions,
            {
                "rule_code": "INC_BRANCH_HIERARCHY_T_MINUS_2",
                "title": "全行正常及当期新撤销机构层级档案纳入",
                "condition": "按规定数据时间窗口纳入全行正常营业的各级分支机构，以及关停日期落入上一工作日至当期申报日的新撤销机构",
                "rationale": "保障监管机构信息层作为全行所有业务报送数据的组织架构根基准确无断层。",
            },
        )
        if "NOT IN ('2', '3', '4', '5', '6')" in upper:
            _add_unique_rule(
                exclusions,
                {
                    "rule_code": "EXC_HISTORICAL_CLOSED_BRANCHES",
                    "title": "剔除往期已撤销或非正常存续的机构记录",
                    "condition": "对已处于撤销停业状态且关停时间早于上期报送窗口的分支机构予以排除",
                    "rationale": "避免已撤并多时的历史分支机构继续作为在营机构上报。",
                },
            )

    # 客户综合授信额度域规则
    if domain == "客户综合授信额度信息监管报送":
        if "CREDITLINE" in upper or "SXDQRQ" in upper or "29991231" in upper:
            _add_unique_rule(
                inclusions,
                {
                    "rule_code": "INC_CREDIT_LINE_AND_CARD_LIMIT",
                    "title": "客户综合授信与信用卡最高额度协议归集",
                    "condition": "归集综合授信客户统一编号（联名授信按最小主客户号合并）与授信到期日期（缺省设长期有效），关联信用卡协议核验存续状态",
                    "rationale": "反映银行向对公及个人客户核定的最高综合授信额度全貌，支持跨表客户编号统一勾稽。",
                },
            )
        if "DUMMY" in upper or "INTERNAL TEST" in upper:
            _add_unique_rule(
                exclusions,
                {
                    "rule_code": "EXC_CREDIT_DUMMY_AND_TEST_RECORDS",
                    "title": "自动剔除虚拟占位与内部测试客户授信记录",
                    "condition": "当客户名称中包含虚拟占位或内部测试特征字样，或关联的信用卡授信在往期已关户时，自动从正式授信报送中剔除",
                    "rationale": "严格防止开发或业务演练使用的测试授信数据混入监管报送引发虚假数据质量通报。",
                },
            )

    return inclusions, exclusions


def _translate_branches(domain: str, chunk: str, comments: List[str]) -> List[Dict[str, Any]]:
    upper = chunk.upper()
    text = f"{upper} {' '.join(comments)}"
    branches: List[Dict[str, Any]] = []

    def _add_branch(title: str, condition: str, action: str) -> None:
        if not any(b["title"] == title for b in branches):
            branches.append({"title": title, "condition": condition, "action": action})

    if "个人卡" in text or "PERSONAL" in text or "CARD_TYPE = 'P'" in upper or "OPS_CLBS_CREDITCARD_CARDNO" in upper:
        _add_branch(
            "个人银行卡卡号与账户双轨匹配分支",
            "当查询请求中传入银行卡号（尤其是信用卡卡号）时走卡号专属匹配通路；传入常规账号时走核心账号匹配通路",
            "自动区分持卡人卡号路径与普通存款账号路径完成客户账户圈定",
        )
    if "对公" in text or "CORP" in text or "CARD_TYPE = 'C'" in upper or "LENGTH(T.ENCODE) = 9" in upper:
        _add_branch(
            "对公结算账户与机构证件位数专项分支",
            "当交易归属对公单位结算账户，或涉外查询使用 9 位组织机构代码证件（非个人港澳通行证）时触发",
            "按企业机构主体及 9 位组织机构代码截取规则映射对公申报要素",
        )
    if "'ALL'" in upper or "统括" in text:
        _add_branch(
            "全币种统括客户兜底分支",
            "当客户报送范围标志设定为全币种覆盖（不限单币种）时触发",
            "免除单币种约束，完整归集该客户下全部外币及本币交易记录",
        )
    if "JOINT_SHARE" in upper or "联名账户" in text:
        _add_branch(
            "跨境联名账户余额分歧处理分支",
            "针对跨境联名账户余额，逻辑中同时存在按约定持有比例拆分与全额归入主申报人两种不同处理路径",
            "目前两套口径在系统中并行存在，对外解释需如实说明各自适用的折算条件",
        )
    if domain == "对公客户信息与境内外身份监管报送":
        if "CHN" in upper and "TWN" in upper and "CUST_NAM_EN" in upper:
            _add_branch(
                "境内外对公客户中英文名称优先取值分支",
                "当对公客户所属国家或地区为中国境内及港澳台时，与境外客户区分采用不同语言名称优先级",
                "境内及港澳台客户优先取中文名称；其他境外客户优先取英文名称",
            )
        if "公司注册证书" in upper or "其他-机构证件" in upper:
            _add_branch(
                "无登记证件对公客户行业兜底分类分支",
                "当对公客户缺少现行有效主证件记录时，按行业类型区分赋予默认证件类别",
                "特定行业客户填报“公司注册证书”，其余无证客户填报“其他-机构证件”",
            )
    if domain == "银行机构信息与层级架构监管报送" and ("PARENT_VIR_NO" in upper or "SOCIAL_CRE_CODE" in upper):
        _add_branch(
            "分支机构证照及金融代码向上继承分支",
            "当基层或虚拟子机构自身缺少银行机构代码、国际汇款代码或统一社会信用代码时自动回溯上级管理机构",
            "逐级向上继承上级管理机构的银行代码与营业执照号，防止机构代码为空",
        )
    if domain == "机构大额结售汇及资金来源与使用报送":
        if "GLACMSP" in text or "对方行号" in text or "开户银行" in text:
            _add_branch(
                "机构大额结售汇交易对手总账账号与异常户名分歧分支",
                "当交易对手账号前 18 位命中内部总账科目时仅截取前 18 位并改用对方行号作为对手名称；部分渠道当对手户名为 0 时改取开户行名称，而其余普通渠道按原对手户名上报",
                "并列揭示普通交易对手与内部总账/异常兜底两种实际逻辑，便于业务理解差异来源",
            )
        if "GTRF" in upper or "GTE" in upper:
            _add_branch(
                "贸易融资与全球市场分部门客户名称与周末兜底分支",
                "按贸易融资、全球市场及支付清算等内部业务条线分路核对大额结售汇客户名称，并在月初特定周五缺失数据时启用兜底批次",
                "贸易融资及市场条线自动关联对公存款户名回填，支付清算条线统一归口核验",
            )
    return branches


def _translate_calculations(domain: str, chunk: str, comments: List[str]) -> Tuple[str, str]:
    upper = chunk.upper()
    joined = " ".join(comments)
    has_amount = "DEALAMOUNT" in upper or "TRANS_AMT" in upper or "交易金额" in joined
    has_balance = "ACCOUNTBALAN" in upper or "BAL_ACCT" in upper or "交易后余额" in joined
    if "JOINT_BAL" in upper or "联名账户" in joined:
        return (
            "CONFLICT",
            "检测到“跨境联名账户折算余额”存在两种并存的计算口径：一处按持有人份额比例折算分摊，另一处将联名账户全额归并计入主申报人名下。",
        )
    if domain == "机构大额结售汇及资金来源与使用报送" and (
        "GLACMSP" in upper or "前18位" in joined or "扭结结果修改为" in joined
    ):
        return (
            "CONFLICT",
            "检测到机构大额结售汇“交易对手名称与账号”存在多口径并行处理：涉及内部总账科目时仅截前 18 位并以对方行号代填对手名称、对手名称为 0 时改取开户银行名称，而常规渠道按原值传递。",
        )
    if has_amount and has_balance:
        return (
            "DIRECT",
            "交易金额与交易后余额均采用交易发生时刻的原值直传模式，不经过跨日滚动累加或日均平均折算。",
        )
    if has_balance:
        return (
            "DIRECT",
            "交易后余额取交易完成瞬间的静态账户余额快照，直接传递展示，区别于日均余额指标。",
        )
    if has_amount:
        return (
            "DIRECT",
            "交易金额按单笔实际发生金额原值传递，不做四舍五入合并截断。",
        )
    if "SUM(" in upper or "COUNT(" in upper or "MIN(" in upper:
        return ("AGGREGATE", "按申报客户、联名主客户或账户维度进行汇总归并统计。")
    return ("DIRECT", "按既定业务规则加工流转，无复杂指标口径改写。")


def _translate_dedup(chunk: str) -> str:
    upper = chunk.upper()
    if "ROW_NUMBER" in upper or "PARTITION BY" in upper:
        return "同一业务主键（客户、账户或交易流水维度）按最新或最优先规则仅保留唯一 1 条记录，杜绝重复报送。"
    if "MERGE INTO" in upper:
        return "按账户或业务唯一标识做增量合并更新，重跑时幂等覆盖，防止数据翻倍。"
    if "SELECT DISTINCT" in upper:
        return "对同一客户与账户维度做整行去重后输出。"
    return ""


def _parse_schema_ddl_by_domain(filename: str, text: str) -> List[ExtractedLogicEvidence]:
    """将全量建表脚本按所属业务领域聚合成精炼的台账结构实证，避免百张建表语句稀释检索结果。"""
    grouped: Dict[str, List[str]] = {}
    for m in re.finditer(r'CREATE\s+TABLE\s+"?[A-Za-z0-9_$.]+"?\."?([A-Za-z0-9_$]+)"?', text, flags=re.IGNORECASE):
        tname = m.group(1).upper()
        domain, _ = _infer_domain_and_concept(tname, tname, [], [tname])
        grouped.setdefault(domain, [])
        if tname not in grouped[domain]:
            grouped[domain].append(tname)

    evidences: List[ExtractedLogicEvidence] = []
    for idx, (domain, tables) in enumerate(grouped.items()):
        fingerprint = hashlib.sha256(f"{filename}:schema_group:{domain}".encode("utf-8")).hexdigest()
        evidences.append(
            ExtractedLogicEvidence(
                logic_fingerprint=fingerprint,
                business_domain_hint=domain,
                target_concept_hint=f"{domain}数据底座结构",
                source_summary=f"支撑「{domain}」的 {len(tables)} 项标准化业务数据台账结构定义。",
                inclusion_predicates=[],
                exclusion_predicates=[],
                branch_cases=[],
                calculation_kind="DIRECT",
                calculation_summary="为对应业务领域提供标准化台账登记结构支撑。",
                dedup_grain_hint="",
                internal_locator={
                    "script": filename,
                    "chunk_index": idx,
                    "start_line": 1,
                    "physical_objects": tables,
                },
            )
        )
    return evidences


def parse_code_logic(
    filename: str,
    content: bytes,
    *,
    llm: Any = None,
    bus: Any = None,
) -> List[ExtractedLogicEvidence]:
    """把 SQL / 存储过程代码切块并结合 sqlglot AST 静默提炼为纯业务语义的底层实证清单。"""
    text = content.decode("utf-8", errors="replace")
    if filename.upper() == "SAC_TABLE_SCHEMAS.SQL" or (
        text.upper().count("CREATE TABLE") >= 10 and text.upper().count("CREATE OR REPLACE") == 0
    ):
        return _parse_schema_ddl_by_domain(filename, text)

    banner = _extract_procedure_banner(text)
    script_regime = infer_regulatory_regime(filename, banner, text[:2000])
    raw_chunks = split_sql_chunks(text)
    evidences: List[ExtractedLogicEvidence] = []
    comments_pool: List[str] = []
    any_sac = False
    single_generic_info: Optional[Tuple[Dict[str, Any], List[str], Dict[str, Any]]] = None

    for idx, (start_line, stmt) in enumerate(raw_chunks):
        upper = stmt.upper()
        if not any(kw in upper for kw in ("INSERT", "MERGE", "UPDATE", "SELECT", "CREATE", "DELETE")):
            continue
        targets = _extract_physical_targets(stmt)
        if _is_housekeeping_chunk(stmt, targets):
            continue
        comments = _extract_comments(stmt)
        comments_pool.extend(comments)
        skeleton = extract_sql_ast_skeleton(stmt)
        merged_targets = list(dict.fromkeys(targets + (skeleton.get("target_tables") or []) + (skeleton.get("source_tables") or [])))

        is_sac = _is_known_sac_logic(filename, banner, comments, merged_targets, stmt)
        if is_sac:
            any_sac = True
            domain, concept = _infer_domain_and_concept(filename, stmt, comments, merged_targets, banner=banner)
            inclusions, exclusions = _translate_predicates(domain, stmt, comments)
            branches = _translate_branches(domain, stmt, comments)
            calc_kind, calc_summary = _translate_calculations(domain, stmt, comments)
            dedup_hint = _translate_dedup(stmt)
            source_summary = next((c for c in comments if "程序描述" in c or "业务说明" in c or "任务描述" in c), "")
            if not source_summary and banner:
                source_summary = banner
            if not source_summary:
                source_summary = f"支撑「{concept}」处理的第 {idx + 1} 项底层逻辑环节。"
            source_summary = re.sub(r"^(?:程序描述|业务说明|任务描述|程序名称)[：:\s]*", "", source_summary).strip()
        else:
            effective_comments = comments or ([banner] if banner else [])
            generic = synthesize_generic_business_semantics(skeleton, effective_comments, filename)
            if not evidences:
                single_generic_info = (skeleton, effective_comments, generic)
            domain = generic["business_domain_hint"]
            concept = generic["target_concept_hint"]
            inclusions = generic["inclusions"]
            exclusions = generic["exclusions"]
            branches = generic["branches"]
            calc_kind = generic["calculation_kind"]
            calc_summary = generic["calculation_summary"]
            dedup_hint = generic["dedup_grain_hint"]
            source_summary = generic["source_summary"]

        fingerprint = hashlib.sha256(f"{filename}:{idx}:{stmt}".encode("utf-8")).hexdigest()
        ev = ExtractedLogicEvidence(
            logic_fingerprint=fingerprint,
            business_domain_hint=domain,
            target_concept_hint=concept,
            source_summary=source_summary,
            inclusion_predicates=inclusions,
            exclusion_predicates=exclusions,
            branch_cases=branches,
            calculation_kind=calc_kind,
            calculation_summary=calc_summary,
            dedup_grain_hint=dedup_hint,
            internal_locator={
                "script": filename,
                "regime": script_regime,
                "chunk_index": idx,
                "start_line": start_line,
                "physical_objects": merged_targets,
                "target_tables": skeleton.get("target_tables") or [],
                "source_tables": skeleton.get("source_tables") or [],
                "ast_parsed": bool(skeleton.get("ast_parsed")),
                "statement_kind": skeleton.get("statement_kind", "UNKNOWN"),
                "join_count": skeleton.get("join_count", 0),
                "ai_refined": False,
            },
        )
        evidences.append(ev)

    if llm is not None and bus is not None and evidences:
        if len(evidences) >= 2:
            evidences = plan_and_extract_script_evidences(
                filename,
                banner,
                evidences,
                comments_pool,
                is_sac_script=any_sac,
                llm=llm,
                bus=bus,
            )
        elif not any_sac and single_generic_info is not None:
            sk, cm, gen = single_generic_info
            translated = translate_evidence_with_llm(sk, cm, gen, llm=llm, bus=bus)
            ev0 = evidences[0]
            ev0.business_domain_hint = translated["business_domain_hint"]
            ev0.target_concept_hint = translated["target_concept_hint"]
            ev0.source_summary = translated["source_summary"]
            ev0.inclusion_predicates = translated["inclusions"]
            ev0.exclusion_predicates = translated["exclusions"]
            ev0.branch_cases = translated["branches"]
            ev0.calculation_kind = translated["calculation_kind"]
            ev0.calculation_summary = translated["calculation_summary"]
            ev0.dedup_grain_hint = translated["dedup_grain_hint"]
            ev0.internal_locator["plan_stage"] = "SINGLE_STEP"
            ev0.internal_locator["ai_refined"] = True
        else:
            enrich_script_evidences_with_llm(
                filename,
                banner,
                evidences,
                comments_pool,
                llm=llm,
                bus=bus,
            )
    return evidences
