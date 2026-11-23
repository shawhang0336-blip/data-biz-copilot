import { api } from './client.js'

export const getPanorama = () => api('/api/assets/panorama')
export const listCapabilities = () => api('/api/assets/capabilities')
export const listRules = (params = {}) => {
  const qs = new URLSearchParams()
  if (params.capability_id) qs.set('capability_id', params.capability_id)
  if (params.rule_category) qs.set('rule_category', params.rule_category)
  if (params.reconcile_status) qs.set('reconcile_status', params.reconcile_status)
  if (params.regime) qs.set('regime', params.regime)
  if (params.sub_domain) qs.set('sub_domain', params.sub_domain)
  if (params.subject_theme) qs.set('subject_theme', params.subject_theme)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return api(`/api/assets/rules${suffix}`)
}
export const editRule = (ruleId, payload) => api(`/api/assets/rules/${ruleId}`, {
  method: 'PATCH',
  body: JSON.stringify(payload),
})
export const confirmRule = (ruleId, payload) => api(`/api/assets/rules/${ruleId}/confirm`, {
  method: 'POST',
  body: JSON.stringify(payload),
})
export const editCaliber = (caliberId, payload) => api(`/api/assets/calibers/${caliberId}`, {
  method: 'PATCH',
  body: JSON.stringify(payload),
})
export const confirmCaliber = (caliberId, payload) => api(`/api/assets/calibers/${caliberId}/confirm`, {
  method: 'POST',
  body: JSON.stringify(payload),
})
export const getAssetBacking = (assetCode) => api(`/api/assets/backing/${encodeURIComponent(assetCode)}`)
