"""dc_agent 事前确定性门禁策略（策略层 Layer 2）。

核心职责：
1. `de_jargon_adjudicator`：零代码黑话门禁——严禁任何业务资产卡片或对外问答文本泄露 SQL 关键字、
   物理表名、字段英文名、存储过程/脚本名等底层实现词汇，被拦截即回喂重述；
2. `evidence_backing_adjudicator`：实证支撑门禁——严禁在无底层逻辑实证或不在白名单依据内时
   宣称“系统逻辑已包含 / 已验证”。
"""

import re
from typing import Any, Dict, List, Optional

from app.kernel.bus import Bus
from app.kernel.run import BudgetGuard, BudgetLimits

# 仅面向技术/工程对象的词汇与标识符模式（严禁出现在对外业务表述中）
# 注意：使用 ASCII 边界 (?<![A-Za-z0-9_]) / (?![A-Za-z0-9_])，因为 Python \b 把中文字符也视作 \w 会漏检“处理SF_SAC_...”
_SQL_KEYWORDS_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:SELECT|FROM|WHERE|INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|FULL\s+JOIN|CROSS\s+JOIN|"
    r"JOIN|GROUP\s+BY|ORDER\s+BY|HAVING|CASE\s+WHEN|NOT\s+EXISTS|EXISTS|ROW_NUMBER|"
    r"PARTITION\s+BY|TRUNCATE|MERGE\s+INTO|INSERT\s+INTO|DELETE\s+FROM|UNION\s+ALL|"
    r"VARCHAR2?|NUMBER|NVL|COALESCE|DECODE|IS\s+NULL|IS\s+NOT\s+NULL|SELECT\s+DISTINCT|"
    r"EXECUTE\s+IMMEDIATE|BETWEEN)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

_PHYSICAL_IDENT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Z][A-Za-z0-9]*_[A-Za-z0-9_]{2,}|DEALAMOUNT|ACCOUNTBALAN|ACCOUNTNO[0-9]*|ACCOUNT_NO|CUST_NO|"
    r"TRANS_AMT|BAL_ACCT|DFVLOC|DFZH|WB_ACCT_NO|DEL_FLAG|"
    r"APPLYSTARTDATE|APPLYENDDATE|APPLYDATE|APPLYNUM|DEALDATE|DEALTIME|DEALCURR|ACCT_CURR|"
    r"CARDNO|CARD_NO|CARD_TYPE|NEED_SUBMIT|CLOSEDATE|ENCODE|"
    r"GRCKFHZMX|GRCKFHZ|DGKHXXB|DGCKFHZ|SXXXB|JGXXB|OPPACCTNO|OPPUSER|GLACMSP|DDACMSP|"
    r"TDACMSP|IPACMSP|EPACMSP|HSMSMP|KHTYBH|NBJGH|YHJGDM|YYZZH|SXDQRQ|GRCKZH|DGCKZH|"
    r"CUSTOMERNAME|REPORTNUMBER|ACTIONTYPE|INSTITUTIONCODE|FCYACCOUNT|LCYACCOUNT|FCYCURRENCY|"
    r"IFFREEZE|CERTTYPE|CERTNAME|TXCCY|SCYY|RPTDATE|COUNTERPARTYACCOUNT|COUNTERPARTYBRANCHNAME|DEALBRANCHNAME)(?![A-Za-z0-9_])"
)

_TECH_WORDS_RE = re.compile(
    r"(?:存储过程|物理表|目标表|暂存表|中间表|临时表|源表|主表|"
    r"字段级血缘|血缘关系|切块|建表语句|表族|脚本|字段直传|主键|外键|跑批|回滚|空表跳脱|落表|取数|"
    r"(?<![A-Za-z0-9_])(?:SQL\s*脚本|SQL\s*语句|SQL|AST|DDL|script|stored\s+procedure)(?![A-Za-z0-9_]))",
    re.IGNORECASE,
)

# SAC 业务字典确定性翻译表（使用 ASCII 边界，防止贴近中文字符时不匹配）
_SAC_BUSINESS_GLOSSARY = (
    (r"(?<![A-Za-z0-9_])SF_SAC_EXCHANGE_DETAILS_INFO_TMP(?![A-Za-z0-9_])", "涉外大额与可疑交易报送暂存台账"),
    (r"(?<![A-Za-z0-9_])SF_SAC_EXCHANGE_DETAILS_INFO(?![A-Za-z0-9_])", "涉外大额与可疑交易报送明细台账"),
    (r"(?<![A-Za-z0-9_])SF_SAC_BASE_ACCOUNT_INFO_TMP_NEW(?![A-Za-z0-9_])", "涉外账户基础信息台账"),
    (r"(?<![A-Za-z0-9_])SF_SAC_BASE_ACCOUNT_INFO_TMP(?![A-Za-z0-9_])", "涉外账户基础信息台账"),
    (r"(?<![A-Za-z0-9_])SF_SAC_BASE_ACCOUNT_INFO(?![A-Za-z0-9_])", "涉外账户基础信息台账"),
    (r"(?<![A-Za-z0-9_])SF_SAC_QUERY_INFO(?![A-Za-z0-9_])", "监管核查查询申请台账"),
    (r"(?<![A-Za-z0-9_])SAC_KHHZH_LIST_A(?![A-Za-z0-9_])", "重点监控客户与账户汇总清单"),
    (r"(?<![A-Za-z0-9_])SAC_KHHZH_LIST(?![A-Za-z0-9_])", "重点监控客户与账户汇总清单"),
    (r"(?<![A-Za-z0-9_])SAC_KHHZH_TMP(?![A-Za-z0-9_])", "客户与账户身份归集清单"),
    (r"(?<![A-Za-z0-9_])SAC_UNREPORT_ZH_LIST_ACC(?![A-Za-z0-9_])", "应报未报风险账户清单"),
    (r"(?<![A-Za-z0-9_])SAC_LIST_ACC(?![A-Za-z0-9_])", "账户报送属地映射清单"),
    (r"(?<![A-Za-z0-9_])SAC_ZH_ZZH(?![A-Za-z0-9_])", "核心账号与拼接账号对照清单"),
    (r"(?<![A-Za-z0-9_])SAC_TMP_DDACMSP(?![A-Za-z0-9_])", "存款账户属地位置归集清单"),
    (r"(?<![A-Za-z0-9_])SAC_TMP_HSMSMP(?![A-Za-z0-9_])", "核心账号类型归集清单"),
    (r"(?<![A-Za-z0-9_])EAST_ODS_GRCKFHZMX_D(?![A-Za-z0-9_])", "个人存款分户账明细报送台账"),
    (r"(?<![A-Za-z0-9_])EAST_ODS_GRCKFHZ_D(?![A-Za-z0-9_])", "个人存款分户账报送台账"),
    (r"(?<![A-Za-z0-9_])EAST_ODS_DGKHXXB_TMP(?![A-Za-z0-9_])", "对公客户信息报送台账"),
    (r"(?<![A-Za-z0-9_])EAST_ODS_DGKHXXB_D(?![A-Za-z0-9_])", "对公客户信息报送台账"),
    (r"(?<![A-Za-z0-9_])EAST_ODS_DGCKFHZ_D(?![A-Za-z0-9_])", "对公存款分户账报送台账"),
    (r"(?<![A-Za-z0-9_])EAST_ODS_JGXXB_D(?![A-Za-z0-9_])", "银行机构信息报送台账"),
    (r"(?<![A-Za-z0-9_])EAST_ODS_SXXXB_TMP(?![A-Za-z0-9_])", "客户授信信息报送台账"),
    (r"(?<![A-Za-z0-9_])EAST_ODS_SXXXB_D(?![A-Za-z0-9_])", "客户授信信息报送台账"),
    (r"(?<![A-Za-z0-9_])SF_SAC_LARGEFUNDS_PFE4ORG_SRC_TMP(?![A-Za-z0-9_])", "机构大额购汇资金来源报送台账"),
    (r"(?<![A-Za-z0-9_])SF_SAC_LARGEFUNDS_PFE4ORG_SRC(?![A-Za-z0-9_])", "机构大额购汇资金来源报送台账"),
    (r"(?<![A-Za-z0-9_])SF_SAC_LARGEFUNDS_SE4ORG_USE_TMP(?![A-Za-z0-9_])", "机构大额结汇资金使用报送台账"),
    (r"(?<![A-Za-z0-9_])SF_SAC_LARGEFUNDS_SE4ORG_USE(?![A-Za-z0-9_])", "机构大额结汇资金使用报送台账"),
    (r"(?<![A-Za-z0-9_])SAC_JGDEJSH_DETALIS_TMP(?![A-Za-z0-9_])", "机构大额结售汇交易明细台账"),
    (r"(?<![A-Za-z0-9_])SAC_JGDEJSH_TMP(?![A-Za-z0-9_])", "机构大额结售汇资金使用台账"),
    (r"(?<![A-Za-z0-9_])GLACMSP(?![A-Za-z0-9_])", "内部总账科目主档"),
    (r"(?<![A-Za-z0-9_])DDACMSP(?![A-Za-z0-9_])", "存款账户主档"),
    (r"(?<![A-Za-z0-9_])TDACMSP(?![A-Za-z0-9_])", "定期存款主档"),
    (r"(?<![A-Za-z0-9_])(?:IPACMSP|EPACMSP)(?![A-Za-z0-9_])", "客户核心账户信息档"),
    (r"(?<![A-Za-z0-9_])HSMSMP(?![A-Za-z0-9_])", "核心账户类型主档"),
    (r"(?<![A-Za-z0-9_])(?:A\.DEALAMOUNT|DEALAMOUNT)(?![A-Za-z0-9_])", "交易金额"),
    (r"(?<![A-Za-z0-9_])(?:A\.ACCOUNTBALAN|ACCOUNTBALAN)(?![A-Za-z0-9_])", "交易后账户余额"),
    (r"(?<![A-Za-z0-9_])APPLYSTARTDATE(?![A-Za-z0-9_])", "申请查询起始日期"),
    (r"(?<![A-Za-z0-9_])APPLYENDDATE(?![A-Za-z0-9_])", "申请查询截止日期"),
    (r"(?<![A-Za-z0-9_])APPLYDATE(?![A-Za-z0-9_])", "申请日期"),
    (r"(?<![A-Za-z0-9_])APPLYNUM(?![A-Za-z0-9_])", "查询申请编号"),
    (r"(?<![A-Za-z0-9_])DEALDATE(?![A-Za-z0-9_])", "交易日期"),
    (r"(?<![A-Za-z0-9_])DEALTIME(?![A-Za-z0-9_])", "交易时间"),
    (r"(?<![A-Za-z0-9_])DEALCURR(?![A-Za-z0-9_])", "交易币种"),
    (r"(?<![A-Za-z0-9_])ACCT_CURR(?![A-Za-z0-9_])", "账户币种"),
    (r"(?<![A-Za-z0-9_])(?:ACCOUNTNO_ZH|ACCOUNTNO_ZZH|ACCOUNTNO1|ACCOUNTNO2|ACCOUNTNO)(?![A-Za-z0-9_])", "客户账号"),
    (r"(?<![A-Za-z0-9_])(?:CARD_NO|CARDNO)(?![A-Za-z0-9_])", "银行卡号"),
    (r"(?<![A-Za-z0-9_])CARD_TYPE(?![A-Za-z0-9_])", "账户卡类型"),
    (r"(?<![A-Za-z0-9_])DFVLOC(?![A-Za-z0-9_])", "报送属地位置标识"),
    (r"(?<![A-Za-z0-9_])NEED_SUBMIT(?![A-Za-z0-9_])", "是否需报送标识"),
    (r"(?<![A-Za-z0-9_])CLOSEDATE(?![A-Za-z0-9_])", "销户日期"),
    (r"(?<![A-Za-z0-9_])KHTYBH(?![A-Za-z0-9_])", "客户统一编号"),
    (r"(?<![A-Za-z0-9_])NBJGH(?![A-Za-z0-9_])", "内部机构号"),
    (r"(?<![A-Za-z0-9_])YHJGDM(?![A-Za-z0-9_])", "银行机构代码"),
    (r"(?<![A-Za-z0-9_])YYZZH(?![A-Za-z0-9_])", "营业执照号"),
    (r"(?<![A-Za-z0-9_])SXDQRQ(?![A-Za-z0-9_])", "授信到期日期"),
    (r"(?<![A-Za-z0-9_])OPPACCTNO(?![A-Za-z0-9_])", "交易对手账号"),
    (r"(?<![A-Za-z0-9_])OPPUSER(?![A-Za-z0-9_])", "交易对手名称"),
    (r"(?<![A-Za-z0-9_])OPS_CLBS_CreditCard_cardno(?![A-Za-z0-9_])", "信用卡卡号匹配标识"),
    (r"(?<![A-Za-z0-9_])OPS_CLBS_CreditCard(?![A-Za-z0-9_])", "信用卡账号匹配标识"),
    (r"(?<![A-Za-z0-9_])SP_SAC_[A-Za-z0-9_]+(?![A-Za-z0-9_])", "系统自动加工流程"),
    (r"(?<![A-Za-z0-9_])SAC-[A-Z]-\d+(?![A-Za-z0-9_])", "业务理解校验场景"),
    (r"(?<![A-Za-z0-9_])sac-account-balance-caliber(?![A-Za-z0-9_])", "「交易后余额口径规范」"),
    (r"(?<![A-Za-z0-9_])sac-deal-amount-caliber(?![A-Za-z0-9_])", "「交易金额口径规范」"),
    (r"(?<![A-Za-z0-9_])sac-reporting-scope-rule(?![A-Za-z0-9_])", "「报送范围圈定规范」"),
    (r"(?<![A-Za-z0-9_])sac-unreported-account-rule(?![A-Za-z0-9_])", "「应报未报账户识别规范」"),
    (r"(?<![A-Za-z0-9_])subject-scope(?![A-Za-z0-9_])", "「报送主体范围口径」"),
    (r"(?<![A-Za-z0-9_])unreported-account(?![A-Za-z0-9_])", "「应报未报账户口径」"),
    (r"(?<![A-Za-z0-9_])deal-amount(?![A-Za-z0-9_])", "「交易金额口径」"),
    (r"(?<![A-Za-z0-9_])deposit-detail(?![A-Za-z0-9_])", "「个人存款分户账明细口径」"),
    (r"(?<![A-Za-z0-9_])(?:TMP_SAC_[A-Za-z0-9_]+|TMP_BDM_[A-Za-z0-9_]+|BDM_[A-Za-z0-9_]+)(?![A-Za-z0-9_])", "上游业务台账"),
    (r"(?<![A-Za-z0-9_])ENCODE(?![A-Za-z0-9_])", "客户证件号码"),
)

# 不检查纯内部编码与枚举字段，只检查对外展示的业务文字字段
_EXEMPT_KEYS = {
    "id",
    "code",
    "rule_code",
    "capability_id",
    "asset_id",
    "evidence_ids",
    "clause_ids",
    "cited_asset_codes",
    "rule_category",
    "status",
    "reconcile_status",
    "coverage_status",
    "triggers",
    "ast_features",
    "statement_kind",
    "tool",
    "args",
    "clause_kind",
    "keep",
    "calculation_kind",
    "reasoning_summary",
    "section_index",
    "document_kind",
    "chunking_strategy",
    "chunk_group",
    "role",
    "group_id",
    "stage_role",
    "step_indices",
}


def detect_technical_jargon(text: str) -> List[str]:
    """扫描文本中残留的技术词汇、SQL 关键字与物理标识符，返回去重命中列表。"""
    if not text or not isinstance(text, str):
        return []
    hits: List[str] = []
    for pattern in (_SQL_KEYWORDS_RE, _PHYSICAL_IDENT_RE, _TECH_WORDS_RE):
        for match in pattern.finditer(text):
            token = match.group(0).strip()
            if token and token not in hits:
                hits.append(token)
    return hits


def sanitize_business_text(text: str) -> str:
    """确定性清洗兜底：先按 SAC 业务字典翻译，再替换残留代码/技术词，确保绝对业务可读。"""
    if not text:
        return ""
    cleaned = text
    for pat, repl in _SAC_BUSINESS_GLOSSARY:
        cleaned = re.sub(pat, repl, cleaned, flags=re.IGNORECASE)
    replacements = (
        (r"存储过程", "系统处理流程"),
        (r"SQL\s*脚本|SQL\s*语句|SQL|脚本", "系统业务处理流程"),
        (r"物理表|目标表|暂存表|中间表|临时表|源表|主表", "业务台账"),
        (r"表族", "业务台账组"),
        (r"字段级血缘|血缘关系", "业务数据流转关系"),
        (r"字段直传", "原值直接传递"),
        (r"主键", "唯一业务标识"),
        (r"外键", "关联业务标识"),
        (r"跑批", "定期批处理"),
        (r"回滚", "异常重算恢复"),
        (r"空表跳脱", "初始空态保护处理"),
        (r"落表", "归档入账"),
        (r"取数", "业务数据采集"),
        (r"NOT\s+EXISTS", "排除校验"),
        (r"ROW_NUMBER", "唯一保留规则"),
        (r"CASE\s+WHEN", "分情形判断"),
        (r"\bBETWEEN\b", "区间匹配"),
        (r"\bINSERT\b|\bSELECT\b|\bJOIN\b|\bWHERE\b|\bNULL\b", ""),
    )
    for pat, repl in replacements:
        cleaned = re.sub(pat, repl, cleaned, flags=re.IGNORECASE)
    cleaned = _SQL_KEYWORDS_RE.sub("", cleaned)
    cleaned = _PHYSICAL_IDENT_RE.sub("相关业务数据项", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([，。；：、）])", r"\1", cleaned)
    return cleaned.strip()


def _iter_business_strings(obj: Any, *, key: str = "") -> List[str]:
    if key in _EXEMPT_KEYS:
        return []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, list):
        items: List[str] = []
        for item in obj:
            items.extend(_iter_business_strings(item))
        return items
    if isinstance(obj, dict):
        items = []
        for k, v in obj.items():
            items.extend(_iter_business_strings(v, key=str(k)))
        return items
    return []


def de_jargon_adjudicator(
    tool_name: str, args: Dict[str, Any], payload: Dict[str, Any]
) -> Optional[str]:
    """零代码黑话门禁：对外业务工具与问答叙述中一旦出现表名、字段名或 SQL 术语立即拒收并反馈改写。"""
    if not isinstance(args, dict):
        return None
    texts = _iter_business_strings(args)
    found: List[str] = []
    for txt in texts:
        for hit in detect_technical_jargon(txt):
            if hit not in found:
                found.append(hit)
    if found:
        return (
            f"去技术化门禁拦截：业务输出中禁止出现代码、SQL 或物理表/字段名称 {found[:6]}，"
            "请完全改写为面向业务人员的日常业务表述。"
        )
    return None


def evidence_backing_adjudicator(
    tool_name: str, args: Dict[str, Any], payload: Dict[str, Any]
) -> Optional[str]:
    """实证依据门禁：核对工具输出的实证/引用编号必须来自本次提供的白名单。"""
    if not isinstance(args, dict) or not isinstance(payload, dict):
        return None
    allowed_evidence = payload.get("allowed_evidence_ids")
    if isinstance(allowed_evidence, list) and "evidence_ids" in args:
        allowed_set = set(allowed_evidence)
        for eid in args.get("evidence_ids") or []:
            if eid not in allowed_set:
                return f"实证幻觉拦截：底层实证编号 `{eid}` 不在本次提取依据清单中"
    allowed_assets = payload.get("allowed_asset_codes")
    if isinstance(allowed_assets, list) and "cited_asset_codes" in args:
        allowed_set = set(allowed_assets)
        for code in args.get("cited_asset_codes") or []:
            if code not in allowed_set:
                return f"业务资产引用越界：资产编号 `{code}` 不在当前检索到的业务资产白名单中"
    return None


def default_bus(parent: Optional[Bus] = None) -> Bus:
    """装配 dc_agent 单次 Run / 问答的独立总线实例（挂齐去技术化与实证门禁）。"""
    bus = Bus(parent=parent)
    bus.add_adjudicator(de_jargon_adjudicator)
    bus.add_adjudicator(evidence_backing_adjudicator)
    return bus


def build_run_bus(
    budget: Optional[Dict[str, Any]] = None,
    *,
    parent: Optional[Bus] = None,
    run_id: Optional[str] = None,
) -> Bus:
    """装配挂载 BudgetGuard（并发次数/Token/超时熔断）的 Run 级门禁总线。"""
    bus = default_bus(parent=parent)
    if budget:
        guard = BudgetGuard(BudgetLimits.from_dict(budget), run_id=run_id)
        guard.attach(bus)
    return bus
