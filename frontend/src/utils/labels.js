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

export function formatBadge(map, value) {
  return map[value] || { label: value || '未知', tone: 'slate' }
}
