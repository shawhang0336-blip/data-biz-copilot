import { api } from './client.js'

export const getHealth = () => api('/api/health')
export const getBudget = () => api('/api/settings/execution-budget')
export const updateBudget = (payload) => api('/api/settings/execution-budget', {
  method: 'PUT',
  body: JSON.stringify(payload),
})
export const getRunDetail = (runId) => api(`/api/runs/${runId}`)
