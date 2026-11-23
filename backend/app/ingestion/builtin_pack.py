"""内置金融监管报送双源样例包与真实 SAC 脚本及监管制度语料加载器（接入层 Layer 3）。"""

import os
from pathlib import Path
from typing import List, Optional, Tuple

BUILTIN_REGULATORY_DOCS: List[Tuple[str, bytes]] = [
    (
        "大额与可疑外汇交易及分户账报送业务制度规范_2026版.md",
        """# 大额与可疑外汇交易报送

## 报送主体纳入范围要求
业务上，大额与可疑外汇交易报送围绕当期申报区间内已纳入重点监控客户汇总清单的主体展开。
只有处于申报开始至结束日期之间、属于重点申报客户及关联账户、且币种符合单币种匹配或全币种统括条件的交易，才应当纳入常规报送范围。

## 个人银行卡、对公账户与统括币种分类处理
报送处理须区分三类业务情形：个人银行卡交易按持卡人身份口径核验；对公结算账户交易按机构主体口径归并；对于核定为全币种统括报送的客户，应完整归集其全部外币及本币交易流水。

## 应报未报风险账户识别与常规报送剔除口径
对于已经发生真实交易但账户暂缺报送属地映射的客户，不得直接当作丢失或作废交易处理，也不得带着残缺属地直接发送监管报送；应统一识别列入「应报未报漏报风险账户清单」跟进补录属地，并在当期常规报送中自动剔除，同时排除当期已报送或已冲正撤回记录。

# 个人存款分户账明细加工

## 个人存款分户账明细客户归并口径
个人存款分户账明细加工用于将有效存款交易事件、账户属性与个人客户基础身份信息进行关联归并，按账户及交易流水主键形成可供下游监管报送与合规核查使用的个人存款分户账明细视图，避免同一笔流水在重复加工时数量翻倍。

# 离岸转口贸易专项监测

## 离岸转口贸易延期收付汇专项核验
根据最新外汇合规补充要求，对于存在超过九十天延期收付汇情形的离岸转口贸易交易，系统应当自动识别标记风险敞口并纳入专项监测报送名单。
""".encode("utf-8"),
    ),
    (
        "监管报送核心指标与业务口径管理办法.md",
        """# 大额与可疑外汇交易报送

## 交易金额公式与口径细则
交易金额反映单笔大额或可疑外汇交易实际发生的本外币金额规模，必须按业务发生时的原始交易金额直传报送，不可经过跨日加总或四舍五入截断；排查金额差异时须先核查主体圈定与币种范围，再核对单笔发生额。

## 交易后余额公式与口径细则
交易后余额反映该笔外汇交易发生完成瞬间账户的静态余额快照，按原值直接传出，属于交易时点快照口径，不可与财务核算的日均余额、月末累计余额混用。

## 跨境联名账户折算余额口径管理
对于多人共有的跨境联名账户，当发生监管大额交易报送时，业务口径须明确统一究竟是按持有人约定比例分摊折算计入各自名下，还是全额归并计入主申报人名下，并在全行报送流程中保持单一口径。
""".encode("utf-8"),
    ),
]

BUILTIN_CODE_SOURCES: List[Tuple[str, bytes]] = [
    (
        "大额与可疑交易报送圈定及分支处理逻辑.sql",
        """/*
 * 程序描述: 大额与可疑外汇交易报送圈定及个人/对公/全币种三分支处理
 */
INSERT INTO SF_SAC_EXCHANGE_DETAILS_INFO (CUST_NO, ACCT_NO, DEALAMOUNT, ACCOUNTBALAN, CURRENCY_CODE)
SELECT DISTINCT A.CUST_NO, A.ACCT_NO, A.DEALAMOUNT, A.ACCOUNTBALAN, A.CURRENCY_CODE
FROM (
    SELECT T.*,
           ROW_NUMBER() OVER (PARTITION BY T.CUST_NO, T.ACCT_NO, T.TX_SEQ ORDER BY T.UPDATE_TS DESC) AS RN
    FROM SF_SAC_EXCHANGE_DETAILS_INFO_TMP T
    INNER JOIN SAC_KHHZH_LIST K ON T.CUST_NO = K.CUST_NO AND T.ACCT_NO = K.ACCT_NO
    WHERE T.DEAL_DATE BETWEEN :START_DATE AND :END_DATE
      AND (K.CURRENCY = T.CURRENCY_CODE OR K.CURRENCY = 'ALL')
      AND T.CARD_TYPE = 'P' -- 个人卡交易分支
      AND T.REPORTED_FLAG <> 'Y'
      AND T.CANCEL_FLAG <> '1'
      AND NOT EXISTS (
          SELECT 1 FROM SAC_UNREPORT_ZH_LIST_ACC U WHERE U.ACCT_NO = T.ACCT_NO
      )
    UNION ALL
    SELECT T.*,
           ROW_NUMBER() OVER (PARTITION BY T.CUST_NO, T.ACCT_NO, T.TX_SEQ ORDER BY T.UPDATE_TS DESC) AS RN
    FROM SF_SAC_EXCHANGE_DETAILS_INFO_TMP T
    INNER JOIN SAC_KHHZH_LIST_A K ON T.CUST_NO = K.CUST_NO
    WHERE T.DEAL_DATE BETWEEN :START_DATE AND :END_DATE
      AND K.CURRENCY = T.CURRENCY_CODE
      AND T.CARD_TYPE = 'C' -- 对公结算账户分支
      AND T.REPORTED_FLAG <> 'Y'
) A
WHERE A.RN = 1;
""".encode("utf-8"),
    ),
    (
        "个人存款分户账明细加工与客户归并逻辑.sql",
        """/*
 * 程序描述: 个人存款分户账明细加工（连接存款交易事件、账户主档与个人客户信息并增量合并）
 */
MERGE INTO EAST_ODS_GRCKFHZMX_D TGT
USING (
    SELECT D.ACCT_NO, D.TRANS_AMT, D.BAL_ACCT, C.CUST_NO
    FROM BDM_EVT_DEPOSIT_TRANS D
    INNER JOIN TMP_BDM_DEPOSIT_ACCT A ON D.ACCT_NO = A.ACCT_NO
    INNER JOIN TMP_BDM_ICUSTOMER C ON A.CUST_NO = C.CUST_NO
    WHERE A.ACCT_STATUS = 'ACTIVE'
) SRC
ON (TGT.DFZH = SRC.ACCT_NO)
WHEN MATCHED THEN UPDATE SET TGT.TRANS_AMT = SRC.TRANS_AMT
WHEN NOT MATCHED THEN INSERT (DFZH, TRANS_AMT) VALUES (SRC.ACCT_NO, SRC.TRANS_AMT);
""".encode("utf-8"),
    ),
    (
        "应报未报漏报风险账户识别与补报清单逻辑.sql",
        """/*
 * 程序描述: 应报未报漏报风险账户识别（捕捉已交易但缺失报送属地映射的待补报账户）
 */
INSERT INTO SAC_UNREPORT_ZH_LIST_ACC (CUST_NO, ACCT_NO)
SELECT T.CUST_NO, T.ACCT_NO
FROM SF_SAC_EXCHANGE_DETAILS_INFO_TMP T
WHERE T.DEAL_DATE BETWEEN :START_DATE AND :END_DATE
  AND T.DFVLOC IS NULL;
""".encode("utf-8"),
    ),
    (
        "交易金额与交易后余额取数及联名账户口径逻辑.sql",
        """/*
 * 程序描述: 交易金额与交易后余额原值快照直传逻辑
 */
INSERT INTO SF_SAC_EXCHANGE_METRICS_SNAP (CUST_NO, DEALAMOUNT, ACCOUNTBALAN)
SELECT A.CUST_NO, A.DEALAMOUNT, A.ACCOUNTBALAN
FROM SF_SAC_EXCHANGE_DETAILS_INFO_TMP A
WHERE A.DEAL_DATE BETWEEN :START_DATE AND :END_DATE;

/*
 * 程序描述: 跨境联名账户折算余额分歧处理分支
 */
INSERT INTO SF_SAC_JOINT_ACCOUNT_SNAP (CUST_NO, JOINT_BAL)
SELECT A.CUST_NO,
       CASE WHEN A.JOINT_SHARE > 0 THEN A.BAL_ACCT * A.JOINT_SHARE ELSE A.BAL_ACCT END AS JOINT_BAL
FROM SF_SAC_EXCHANGE_DETAILS_INFO_TMP A
WHERE A.DEAL_DATE BETWEEN :START_DATE AND :END_DATE;
""".encode("utf-8"),
    ),
]

OFFICIAL_SAC_REGULATORY_DOCS: List[Tuple[str, bytes]] = [
    (
        "国家外汇管理局_银行结售汇统计与机构大额结售汇资金来源使用报送规范.md",
        """# 机构大额结售汇及资金来源与使用报送

## 银行大额结售汇与购汇资金来源及结汇资金使用总体要求
根据国家外汇管理局现行银行结售汇统计与真实性合规审查要求，银行业金融机构在办理机构大额购汇与结汇业务时，必须按日监测大额结售汇头寸与资金流向，严格核验机构购汇人民币资金来源的合法真实性及机构结汇后人民币资金使用的合规去向，并按统一变动编号按日采集与补录报送。

## 机构 30 万美元大额结售汇监测及变动编号管理口径
达到机构 30 万美元及以上重点监测门槛的结汇资金使用与购汇资金来源交易，须涵盖贸易融资、全球市场及支付清算各条线；同一机构同一交易日内上报的每笔大额结售汇记录必须生成唯一变动编号标识，当日重跑及人工补录时自动覆盖清理旧批次防止重复计数，并清洗对手户名与币种全半角空格。

## 机构大额结售汇交易对手户名与总账科目口径规范
监管要求如实准确填报机构大额结售汇交易对手名称与对手账号。若交易对手账号实际归属银行内部总账会计科目，应明确业务上究竟是按截取后的内部总账标识配对方行号填报、对对手名称为空或零值的记录代填开户银行名称，还是统一按真实交易对手全称直传，全行报送口径须一致。
""".encode("utf-8"),
    ),
    (
        "国家金融监督管理总局_EAST5.0银行业监管数据标准化规范业务指引.md",
        """# 银行机构信息与层级架构监管报送

## 银行机构信息表覆盖范围与上下级代码规范
在 EAST 5.0 公共信息域中，机构信息表是全行所有监管报送数据向上汇总与向下穿透的根基。应当完整报送各级营业机构与虚拟专营机构（涵盖境内及境外分支机构）的银行机构代码、内部机构号、营业执照号及机构名称，并仅保留正常营业及当期新撤销机构；当子机构自身缺失金融代码或营业执照号时，须明确向上级管理机构继承补全，防止出现孤儿机构断层。

# 对公客户信息与境内外身份监管报送

## 对公客户信息纳入与授信存续捞回及境内外规则
在 EAST 5.0 客户信息域中，对公客户信息表须与授信信息表、对公存款及信贷表的客户统一编号保持绝对勾稽一致。所有正常对公客户及当期新关户客户均应纳入，对已被标记拟删除但当日仍有存续有效授信的对公客户应主动捞回保留报送；境内及港澳台客户优先取中文名称、境外客户优先取英文名称，并准确识别境内居民与境外非居民身份及主证件类别。

# 客户综合授信额度信息监管报送

## 客户综合授信额度口径与非真实测试数据剔除
在 EAST 5.0 信贷管理域中，授信信息表集中反映银行向对公客户与个人（含信用卡协议）核定的最高综合授信额度及授信到期日期（联名授信按单一主客户统一归并），用于与具体借据和合同勾稽。正式报送前必须严格排查并自动剔除客户名称含虚拟占位或内部测试字样的演练记录，严防测试数据污染监管报表。

# 个人存款分户账明细加工

## 个人存款分户账及明细归并与个体工商户分类口径
在 EAST 5.0 会计记账域中，个人存款分户账及明细负责衔接总账与逐笔流水，按有效存款账户与个人客户信息归并（联名存款按唯一主客户归并）。按照监管分类口径，以机构名义或对公证件开立账户的个体工商户必须从个人存款分户账中剔除并归入对公维度；同时清理早于迁移基准日已销户的历史遗留迁移账户及测试账户，仅报送存续及当期新销户账户。

# 一表通互联网贷款与信贷资产转让报送

## 一表通互联网贷款协议及信贷资产转让报送映射要求
按照国家金融监督管理总局推进监管报表一表通建设与银行数字金融高质量发展要求，商业银行除现有 EAST 报送主题域外，还应逐步落地互联网贷款合作协议、互联网贷款合同附加要素及信贷资产转让台账等一表通标准产品报送映射，实现监管数据统一标准、统一采集与统一管理。
""".encode("utf-8"),
    ),
    (
        "中国人民银行_大额与可疑交易报告2025年修订补充及数字人民币要求.md",
        """# 数字人民币大额与可疑交易监管报送

## 数字人民币大额交易和可疑交易监测报告新增要求
依据中国人民银行令〔2025〕第10号关于修改《金融机构大额交易和可疑交易报告管理办法》的决定（第二十五条修订），银行及从事支付清算业务的机构应当建立健全覆盖数字人民币业务场景的大额交易和可疑交易监测分析及报告机制，目前该项数字人民币专项大额与可疑交易报送要求已由监管明确提出。
""".encode("utf-8"),
    ),
]


def default_sql_explorer_dir() -> str:
    """默认从环境变量 `SQL_EXPLORER_DIR` 或同级工作区 `../sql_explorer` 查找真实 SAC 语料。"""
    return os.environ.get("SQL_EXPLORER_DIR") or str(Path(__file__).resolve().parents[4] / "sql_explorer")


def collect_real_sac_materials(
    sql_explorer_dir: Optional[str] = None,
) -> Tuple[List[Tuple[str, bytes]], List[Tuple[str, bytes]]]:
    """收集 sql_explorer 中的真实 25 份 SAC SQL 脚本 + 真实 SAC 制度/口径文件 + 官方监管补充文本。"""
    target_dir = sql_explorer_dir or default_sql_explorer_dir()
    root = Path(target_dir)
    if not root.exists():
        raise FileNotFoundError(f"找不到真实 SAC 语料目录：{target_dir}")

    reg_docs: List[Tuple[str, bytes]] = list(OFFICIAL_SAC_REGULATORY_DOCS)
    code_sources: List[Tuple[str, bytes]] = []

    reg_paths: List[Path] = []
    for sub_pattern in (
        "metrics/sac-*.md",
        "domains/sac-reporting/terms/*.md",
        "domains/sac-reporting/entities/*.md",
    ):
        for p in sorted(root.glob(sub_pattern)):
            if p.name != "README.md":
                reg_paths.append(p)

    # 引入官方文档汇编中与 SAC 强相关且代表完整监管条文的正式件
    for doc_name in (
        "金融机构大额交易和可疑交易报告管理办法_现行汇编版.md",
        "大额交易和可疑交易报告办法修改决定_央行令2025第10号.md",
        "银行保险监管统计管理办法_2022年第10号令.md",
        "EAST监管标准化数据处罚通报_银保监会2022.md",
        "一表通_数字金融高质量发展实施方案_金监总局办公厅2025.md",
    ):
        cand = root / "docs" / "regulatory" / doc_name
        if cand.exists():
            reg_paths.append(cand)

    for p in reg_paths:
        reg_docs.append((p.name, p.read_bytes()))

    for sql_path in sorted((root / "sample_scripts").glob("*SAC*.sql")):
        code_sources.append((sql_path.name, sql_path.read_bytes()))

    return reg_docs, code_sources
