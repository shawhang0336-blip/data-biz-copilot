"""零外置依赖的纵横双维金融业务分类器（基础设施层 Layer 0）。

提供两套正交且完全去技术化的分类能力：
1. 横轴「金融十大主题（subject_theme）」：借鉴银行业标准企业级数仓主题域视角，
   支持跨 1104 / EAST / SAFE / PLUS / 一表通 等监管体系横向对比同一金融主题的规则与口径；
2. 纵轴「二级业务子专题（sub_domain）」：在一级监管业务域（Capability）内部继续按业务专题细分，
   解决单域百条规则混杂以及 Stage 3 融合截断问题。
"""

import re
from typing import Dict, List, Sequence, Tuple

# 标准金融数仓十大主题（纯业务中文表述，严禁含技术/表/字段术语）
FINANCIAL_SUBJECT_THEMES: Tuple[str, ...] = (
    "当事人与客户",
    "协议与账户",
    "交易与事件",
    "财务与余额",
    "授信与押品",
    "机构与组织",
    "产品与业务",
    "渠道与媒介",
    "地域与跨境",
    "风控与合规管控",
)

_THEME_EXPLICIT_ALIASES: Tuple[Tuple[str, str], ...] = (
    ("当事人与客户", "当事人与客户"),
    ("当事人主题", "当事人与客户"),
    ("客户主题", "当事人与客户"),
    ("协议与账户", "协议与账户"),
    ("协议主题", "协议与账户"),
    ("账户主题", "协议与账户"),
    ("交易与事件", "交易与事件"),
    ("交易主题", "交易与事件"),
    ("事件主题", "交易与事件"),
    ("财务与余额", "财务与余额"),
    ("财务主题", "财务与余额"),
    ("余额主题", "财务与余额"),
    ("授信与押品", "授信与押品"),
    ("授信主题", "授信与押品"),
    ("押品主题", "授信与押品"),
    ("机构与组织", "机构与组织"),
    ("机构主题", "机构与组织"),
    ("组织主题", "机构与组织"),
    ("产品与业务", "产品与业务"),
    ("产品主题", "产品与业务"),
    ("渠道与媒介", "渠道与媒介"),
    ("渠道主题", "渠道与媒介"),
    ("媒介主题", "渠道与媒介"),
    ("地域与跨境", "地域与跨境"),
    ("地域主题", "地域与跨境"),
    ("跨境主题", "地域与跨境"),
    ("风控与合规管控", "风控与合规管控"),
    ("风控主题", "风控与合规管控"),
    ("合规管控", "风控与合规管控"),
)

_THEME_KEYWORDS: Dict[str, Tuple[Tuple[str, int], ...]] = {
    "当事人与客户": (
        ("对公客户", 4),
        ("个人客户", 4),
        ("重点客户", 4),
        ("客户信息", 4),
        ("客户类型", 3),
        ("客户类别", 3),
        ("客户身份", 4),
        ("收款主体", 3),
        ("客户编号", 3),
        ("客户名称", 3),
        ("证件号码", 3),
        ("港澳通行证", 4),
        ("统一社会信用代码", 3),
        ("组织机构代码", 3),
        ("当事人", 4),
        ("个人", 2),
        ("对公", 2),
        ("客户", 2),
        ("主体", 2),
        ("证件", 2),
    ),
    "协议与账户": (
        ("联名账户", 5),
        ("分户账", 4),
        ("同业存放", 4),
        ("同业拆放", 4),
        ("定期存单", 4),
        ("账户基础信息", 4),
        ("账户号码", 3),
        ("未销户", 4),
        ("销户", 3),
        ("开户", 3),
        ("活期", 2),
        ("账户", 2),
        ("协议", 3),
        ("合约", 3),
        ("合同", 2),
    ),
    "交易与事件": (
        ("大额结售汇", 4),
        ("购汇资金来源", 4),
        ("结汇资金使用", 4),
        ("结售汇", 4),
        ("大额交易", 3),
        ("交易对手", 3),
        ("资金来源", 3),
        ("资金使用", 3),
        ("申报编号", 3),
        ("汇入汇款", 3),
        ("汇款", 3),
        ("结汇", 3),
        ("购汇", 3),
        ("收支", 2),
        ("发生额", 3),
        ("交易", 2),
        ("事件", 2),
    ),
    "财务与余额": (
        ("账面余额", 4),
        ("交易后余额", 4),
        ("交易金额", 3),
        ("美元金额", 3),
        ("本金余额", 4),
        ("同业存放净额", 4),
        ("折算余额", 4),
        ("净额", 4),
        ("余额", 3),
        ("金额", 2),
        ("累加", 2),
        ("汇总", 2),
        ("总账", 3),
        ("核算", 3),
        ("折算", 2),
        ("财务", 3),
    ),
    "授信与押品": (
        ("质押定期存单", 8),
        ("五级分类", 5),
        ("不良贷款", 5),
        ("已质押", 6),
        ("质押", 6),
        ("押品", 5),
        ("授信", 5),
        ("核销", 4),
        ("次级", 3),
        ("可疑", 2),
        ("损失", 3),
        ("抵押", 5),
        ("担保", 5),
        ("对公贷款", 4),
        ("贷款", 3),
        ("信贷", 4),
        ("额度", 3),
        ("保证金", 3),
    ),
    "机构与组织": (
        ("银行机构", 4),
        ("分支机构", 4),
        ("层级架构", 4),
        ("上级管理机构", 4),
        ("上级机构", 4),
        ("内部往来清算", 4),
        ("内部清算", 4),
        ("报送属地", 3),
        ("属地映射", 3),
        ("营业执照", 4),
        ("对方行号", 3),
        ("开户行", 3),
        ("机构", 2),
        ("支行", 3),
        ("组织", 3),
    ),
    "产品与业务": (
        ("数字人民币", 4),
        ("互联网贷款", 4),
        ("信贷资产转让", 4),
        ("一表通", 3),
        ("贸易融资", 4),
        ("全球市场", 3),
        ("理财产品", 4),
        ("理财", 3),
        ("赎回", 3),
        ("业务品种", 3),
        ("产品", 3),
    ),
    "渠道与媒介": (
        ("手工补录", 5),
        ("人工补录", 5),
        ("自动采集", 3),
        ("网上银行", 4),
        ("网银", 4),
        ("柜面", 4),
        ("双渠道", 4),
        ("渠道", 4),
        ("银行卡", 4),
        ("信用卡", 4),
        ("卡号", 3),
        ("媒介", 3),
        ("线上", 3),
    ),
    "地域与跨境": (
        ("离岸转口贸易", 5),
        ("转口贸易", 4),
        ("跨境联名", 4),
        ("涉外收入", 4),
        ("国际收支", 4),
        ("境内外", 4),
        ("港澳台", 4),
        ("外汇", 3),
        ("境外", 3),
        ("境内", 2),
        ("跨境", 4),
        ("涉外", 3),
        ("离岸", 3),
        ("地域", 3),
        ("国别", 3),
        ("币种", 2),
    ),
    "风控与合规管控": (
        ("应报未报", 5),
        ("漏报风险", 5),
        ("审核通过", 5),
        ("已批准", 4),
        ("复核通过", 5),
        ("审批", 4),
        ("复核", 3),
        ("防重", 4),
        ("去重", 4),
        ("幂等", 4),
        ("测试状态", 4),
        ("测试账户", 4),
        ("模拟演练", 4),
        ("豁免", 4),
        ("捞回", 4),
        ("核查", 3),
        ("免申报", 3),
        ("门禁", 3),
        ("剔除", 2),
        ("排除", 2),
        ("合规", 2),
        ("风控", 4),
    ),
}

_GENERIC_STAGE_NAMES = {
    "",
    "核心业务规则",
    "核心业务规则与逻辑加工",
    "通用业务规范",
    "通用业务数据处理域",
    "待ai归类监管逻辑域",
    "全流程阶段",
    "单阶段处理",
}


def normalize_financial_theme(value: str) -> str:
    """将输入规范化为标准金融十大主题之一，无法识别时返回空串。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if text in FINANCIAL_SUBJECT_THEMES:
        return text
    for alias, canonical in _THEME_EXPLICIT_ALIASES:
        if alias == text or alias in text:
            return canonical
    return ""


def detect_query_financial_themes(query: str) -> List[str]:
    """从自然语言提问中提取用户显式指定的金融十大主题列表（保持出现顺序）。"""
    q = str(query or "").strip()
    if not q:
        return []
    hits: List[Tuple[int, str]] = []
    seen = set()
    for alias, canonical in _THEME_EXPLICIT_ALIASES:
        pos = q.find(alias)
        if pos >= 0 and canonical not in seen:
            seen.add(canonical)
            hits.append((pos, canonical))
    hits.sort(key=lambda item: item[0])
    return [canonical for _, canonical in hits]


def detect_query_financial_theme(query: str) -> str:
    """从自然语言提问中提取首个显式指定的金融十大主题（无则返回空串）。"""
    hits = detect_query_financial_themes(query)
    return hits[0] if hits else ""


def infer_financial_theme(
    title: str,
    condition: str = "",
    action: str = "",
    capability_name: str = "",
    rule_category: str = "",
) -> str:
    """结合标题、执行条件、处理动作、能力域与规则类别，将资产归入最贴切的金融十大主题。"""
    t = str(title or "")
    detail = f"{condition or ''} {action or ''}"
    cap = str(capability_name or "")
    cat = str(rule_category or "").upper()

    scores: Dict[str, int] = {theme: 0 for theme in FINANCIAL_SUBJECT_THEMES}
    for theme, kws in _THEME_KEYWORDS.items():
        sc = 0
        for kw, weight in kws:
            if kw in t:
                sc += weight * 3
            if kw in detail:
                sc += weight * 2
            if kw in cap:
                sc += weight
        scores[theme] = sc

    if cat == "DEDUP":
        scores["风控与合规管控"] += 8

    best_theme = max(
        FINANCIAL_SUBJECT_THEMES,
        key=lambda theme: (scores[theme], -FINANCIAL_SUBJECT_THEMES.index(theme)),
    )
    if scores[best_theme] <= 0:
        if cat == "EXCLUSION":
            return "风控与合规管控"
        return "交易与事件"
    return best_theme


def _clean_stage_candidate(stage_title: str, domain_name: str) -> str:
    s = re.sub(r"^(?:阶段[一二三四五0-9]*|步骤[0-9]*)[：:\s-]*", "", str(stage_title or "")).strip()
    s = re.sub(r"逻辑加工阶段$|逻辑阶段$", "", s).strip()
    if not s or s.lower() in _GENERIC_STAGE_NAMES or s == domain_name:
        return ""
    if 4 <= len(s) <= 22:
        return s
    return ""


def infer_sub_domain(
    domain_name: str,
    title: str,
    condition: str = "",
    action: str = "",
    *,
    target_concept: str = "",
    stage_title: str = "",
    rule_category: str = "",
) -> str:
    """在纵轴一级业务域内部切分清晰可读的二级业务子专题（零技术黑话）。"""
    dom = str(domain_name or "")
    t = str(title or "")
    blob = f"{t} {condition or ''} {action or ''} {target_concept or ''}"
    cat = str(rule_category or "").upper()

    # 1) 机构大额结售汇及资金来源与使用
    if "结售汇" in dom or "资金来源" in dom or "资金使用" in dom:
        if any(k in blob for k in ("购汇", "资金来源")):
            return "购汇资金来源与申报映射"
        if any(k in blob for k in ("结汇", "资金使用")):
            return "结汇资金使用与去向校验"
        if any(k in blob for k in ("对手", "总账", "前 18", "前18", "行号", "开户行", "户名")):
            return "交易对手与总账映射"
        if any(k in blob for k in ("补录", "人工", "覆盖")):
            return "手工补录与人工修正"
        if any(k in blob for k in ("贸易融资", "全球市场", "支付清算", "周末", "分部门", "兜底")):
            return "业务条线与兜底核验"
        if any(k in blob for k in ("门槛", "变动编号", "30万", "三十万", "大额")):
            return "大额门槛与变动编号核验"
        return "结售汇范围与通用筛选"

    # 2) 大额与可疑外汇交易报送
    if "大额与可疑外汇" in dom or ("大额" in dom and "可疑" in dom and "数字人民币" not in dom):
        if any(k in blob for k in ("联名", "份额", "持有比例")):
            return "跨境联名账户折算"
        if any(k in blob for k in ("余额", "交易金额", "快照", "折算", "币种")):
            return "交易金额与余额口径"
        if any(k in blob for k in ("重点", "客户清单", "港澳", "通行证", "证件", "组织机构代码", "卡号", "三路")):
            return "重点客户与证件归集"
        if any(k in blob for k in ("销户", "未销户", "99991231", "账户基础")):
            return "账户基础信息与销户态"
        if any(k in blob for k in ("查询", "申请", "柜面", "网银", "双渠道", "排名")):
            return "监管核查与双渠道监测"
        return "申报窗口与范围准入"

    # 3) 对公客户信息与境内外身份
    if "对公客户" in dom or "境内外身份" in dom:
        if any(k in blob for k in ("捞回", "境内外", "中文名", "英文名", "港澳台", "中英文")):
            return "境内外身份识别与捞回"
        if any(k in blob for k in ("无证", "注册证书", "行业", "证件类别")):
            return "无证客户与证件分类兜底"
        return "对公客户准入与有效态"

    # 4) 个人存款分户账
    if "分户账" in dom or "个人存款" in dom:
        if any(k in blob for k in ("迁移", "迁徙", "个体工商户")):
            return "迁移资产与个体户标识"
        if any(k in blob for k in ("防重", "去重", "唯一", "幂等")):
            return "分户账记录防重与校验"
        return "个人存款分户账明细加工"

    # 5) 1104 同业存放与拆放核算
    if "1104" in dom or "同业存放" in dom:
        if any(k in blob for k in ("质押", "冻结", "扣减", "净额", "账面余额", "累加", "汇总", "分组", "计算")) or cat == "BRANCH":
            return "质押扣减与净额核算"
        return "有效账户准入与剔除"

    # 6) PLUS 手工补录与人工覆盖
    if "PLUS" in dom or "手工补录" in dom:
        if any(k in blob for k in ("未匹配", "找不到", "不存在", "新增", "忽略")):
            return "未匹配记录与只改不增"
        if any(k in blob for k in ("审核", "审批", "复核", "批准", "退回")):
            return "复核审批门禁控制"
        return "匹配覆盖与幂等更新"

    # 7) EAST 对公贷款五级分类 / 授信
    if "五级分类" in dom or "不良贷款" in dom:
        if any(k in blob for k in ("核销", "演练", "模拟", "剔除", "排除")) and "档" not in t:
            return "核销与演练合同剔除"
        if any(k in blob for k in ("汇总", "累加", "防重", "唯一", "归并")) or cat == "DEDUP":
            return "贷款分级汇总与防重"
        return "不良档次筛选与准入"

    if "授信" in dom:
        if any(k in blob for k in ("额度", "指标", "计算", "汇总", "净额")):
            return "授信额度汇总与计算口径"
        return "授信主体与合同筛选"

    # 8) SAFE 涉外收支 & 应报未报
    if "应报未报" in dom or "漏报" in dom:
        if any(k in blob for k in ("待补报", "补报", "隔离", "剔除")):
            return "待补报识别与常规隔离"
        return "属地映射核验"

    if "SAFE" in dom or "涉外收入" in dom or "国际收支" in dom:
        if any(k in blob for k in ("免申报", "五千", "5000", "门槛", "机构", "对公", "私")):
            return "免申报阈值与主体判定"
        if cat == "DEDUP" or any(k in blob for k in ("逐笔", "防重", "去重")):
            return "逐笔直传与唯一校验"
        return "涉外收入申报范围"

    # 9) 银行机构信息
    if "机构信息" in dom or "层级架构" in dom:
        if any(k in blob for k in ("继承", "上级", "代码", "营业执照", "统一社会信用")):
            return "层级继承与代码补全"
        return "机构基础信息准入"

    # 10) 通用兜底（优先采纳 AI 规划阶段名，再按规则类别归类）
    stage_cand = _clean_stage_candidate(stage_title, dom)
    if stage_cand:
        return stage_cand

    if cat == "CALIBER" or any(k in blob for k in ("计算", "公式", "净额", "余额", "汇总", "指标")):
        return "指标核算与口径计算"
    if cat == "EXCLUSION":
        return "例外剔除与风险隔离"
    if cat == "BRANCH":
        return "多情形分类与分支"
    if cat == "DEDUP":
        return "防重合并与幂等控制"
    return "准入圈定与主体筛选"


def summarize_subject_theme_matrix(
    rules: Sequence[Dict[str, object]],
    calibers: Sequence[Dict[str, object]],
) -> List[Dict[str, object]]:
    """按标准金融十大主题汇总全库规则数、口径数及跨越的能力域数。"""
    bucket: Dict[str, Dict[str, object]] = {
        theme: {"theme": theme, "rule_count": 0, "caliber_count": 0, "capabilities": set()}
        for theme in FINANCIAL_SUBJECT_THEMES
    }
    for r in rules:
        theme = normalize_financial_theme(str(r.get("subject_theme") or "")) or infer_financial_theme(
            str(r.get("title") or ""),
            str(r.get("business_condition") or ""),
            str(r.get("business_action") or ""),
            str(r.get("capability_name") or ""),
            str(r.get("rule_category") or ""),
        )
        entry = bucket[theme]
        entry["rule_count"] = int(entry["rule_count"]) + 1
        if r.get("capability_name"):
            entry["capabilities"].add(str(r["capability_name"]))
    for c in calibers:
        theme = normalize_financial_theme(str(c.get("subject_theme") or "")) or infer_financial_theme(
            str(c.get("metric_name") or ""),
            str(c.get("business_definition") or ""),
            str(c.get("formula_summary") or ""),
            str(c.get("capability_name") or ""),
            "CALIBER",
        )
        entry = bucket[theme]
        entry["caliber_count"] = int(entry["caliber_count"]) + 1
        if c.get("capability_name"):
            entry["capabilities"].add(str(c["capability_name"]))

    out: List[Dict[str, object]] = []
    for theme in FINANCIAL_SUBJECT_THEMES:
        entry = bucket[theme]
        caps = sorted(entry["capabilities"])
        out.append(
            {
                "theme": theme,
                "rule_count": entry["rule_count"],
                "caliber_count": entry["caliber_count"],
                "total_assets": int(entry["rule_count"]) + int(entry["caliber_count"]),
                "capability_count": len(caps),
                "capabilities": caps,
            }
        )
    return out
