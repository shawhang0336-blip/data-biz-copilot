export const COVERAGE_LABELS = {
  COVERED: { label: '系统逻辑已涵盖', tone: 'green' },
  PARTIAL: { label: '存在多套并行口径', tone: 'amber' },
  UNREALIZED: { label: '仅制度提及尚未包含', tone: 'slate' },
}

export const RECONCILE_LABELS = {
  VERIFIED: { label: '逻辑与制度一致', tone: 'green' },
  CONSISTENT: { label: '系统逻辑已有实现', tone: 'blue' },
  DIVERGENT: { label: '存在多套口径差异', tone: 'amber' },
  UNREALIZED: { label: '当前系统尚未包含', tone: 'slate' },
}

export const RULE_CATEGORY_LABELS = {
  INCLUSION: '纳入范围',
  EXCLUSION: '自动剔除',
  BRANCH: '分支处理',
  DEDUP: '防重保留',
}

export const STATUS_LABELS = {
  DRAFT: { label: '系统自动还原', tone: 'blue' },
  CONFIRMED: { label: '已补充业务释义', tone: 'green' },
}

export const BUDGET_LABELS = {
  max_retries: '门禁拦截最大重述次数',
  max_llm_concurrency: '模型并发调用上限',
  max_llm_calls: '单轮问答最大调用次数',
  max_total_tokens: '单任务累计 Token 上限',
  max_elapsed_seconds: '单次解析/问答超时秒数',
}

export const FINANCIAL_SUBJECT_THEMES = [
  '当事人与客户',
  '协议与账户',
  '交易与事件',
  '财务与余额',
  '授信与押品',
  '机构与组织',
  '产品与业务',
  '渠道与媒介',
  '地域与跨境',
  '风控与合规管控',
]

export const FINANCIAL_SUBJECT_THEME_DESCRIPTIONS = {
  当事人与客户: '对公与个人客户主体、证件信息归集及重点监控名单',
  协议与账户: '同业存放、分户账、联名账户、活定期合约及销户状态',
  交易与事件: '大额结售汇、跨境收支汇款、资金来源与使用及变动行为',
  财务与余额: '账面余额、净额扣减、交易金额、静态余额快照与汇总核算',
  授信与押品: '对公信贷五级分类、综合授信额度、质押存单扣减与核销剔除',
  机构与组织: '分支机构层级继承、营业执照信息、属地映射及内部往来清算',
  产品与业务: '数字人民币、互联网贷款、信贷资产转让、理财与条线产品',
  渠道与媒介: '手工补录与人工修正、银行卡媒介及柜面/网银双渠道汇总',
  地域与跨境: '离岸转口贸易、涉外收支、港澳台识别及境内外币种口径',
  风控与合规管控: '复核审批门禁、测试/演练与应报未报剔除、防重及幂等控制',
}

export function formatBadge(map, value) {
  return map[value] || { label: value || '未知', tone: 'slate' }
}
