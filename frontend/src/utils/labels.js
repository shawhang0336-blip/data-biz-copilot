export const COVERAGE_LABELS = {
  COVERED: { label: '系统逻辑已涵盖', tone: 'green' },
  PARTIAL: { label: '部分分支有分歧', tone: 'amber' },
  UNREALIZED: { label: '仅制度提及尚未包含', tone: 'slate' },
}

export const RECONCILE_LABELS = {
  VERIFIED: { label: '逻辑与制度一致', tone: 'green' },
  CONSISTENT: { label: '系统逻辑已有实现', tone: 'blue' },
  DIVERGENT: { label: '存在多套口径分歧', tone: 'amber' },
  UNREALIZED: { label: '当前系统尚未包含', tone: 'slate' },
}

export const RULE_CATEGORY_LABELS = {
  INCLUSION: '纳入范围',
  EXCLUSION: '自动剔除',
  BRANCH: '分支处理',
  DEDUP: '防重保留',
}

export const STATUS_LABELS = {
  DRAFT: { label: '待业务确认', tone: 'amber' },
  CONFIRMED: { label: '业务已确认', tone: 'green' },
}

export function formatBadge(map, value) {
  return map[value] || { label: value || '未知', tone: 'slate' }
}
