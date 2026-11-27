import { api } from './client.js'

export const getHealth = () => api('/api/health')
export const getBudget = () => api('/api/settings/execution-budget')
export const updateBudget = (payload) => api('/api/settings/execution-budget', {
  method: 'PUT',
  body: JSON.stringify(payload),
})
export const getLlmConfig = () => api('/api/settings/llm-config')
export const updateLlmConfig = (payload) => api('/api/settings/llm-config', {
  method: 'PUT',
  body: JSON.stringify(payload),
})
export const getRunDetail = (runId) => api(`/api/runs/${runId}`)

export const getAuditOverview = ({ runKind, limit } = {}) => {
  const qs = new URLSearchParams()
  if (runKind && runKind !== 'ALL') qs.set('run_kind', runKind)
  if (limit) qs.set('limit', String(limit))
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return api(`/api/runs/audit-overview${suffix}`)
}

export const getAuditRunDetail = (runId) => api(`/api/runs/${runId}/audit-detail`)
