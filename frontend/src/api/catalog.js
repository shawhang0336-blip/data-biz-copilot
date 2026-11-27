import { api } from './client.js'

export const getPanorama = () => api('/api/assets/panorama')

export const listCapabilities = (params = {}) => {
  const qs = new URLSearchParams()
  if (params.agency && params.agency !== 'ALL') qs.set('agency', params.agency)
  if (params.regime && params.regime !== 'ALL') qs.set('regime', params.regime)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return api(`/api/assets/capabilities${suffix}`)
}

export const listRules = (params = {}) => {
  const qs = new URLSearchParams()
  if (params.capability_id) qs.set('capability_id', params.capability_id)
  if (params.rule_category) qs.set('rule_category', params.rule_category)
  if (params.reconcile_status) qs.set('reconcile_status', params.reconcile_status)
  if (params.agency && params.agency !== 'ALL') qs.set('agency', params.agency)
  if (params.regime && params.regime !== 'ALL') qs.set('regime', params.regime)
  if (params.sub_domain) qs.set('sub_domain', params.sub_domain)
  if (params.subject_theme) qs.set('subject_theme', params.subject_theme)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return api(`/api/assets/rules${suffix}`)
}

export const listReconcileLedger = (params = {}) => {
  const qs = new URLSearchParams()
  if (params.agency && params.agency !== 'ALL') qs.set('agency', params.agency)
  if (params.regime && params.regime !== 'ALL') qs.set('regime', params.regime)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return api(`/api/assets/reconcile${suffix}`)
}

export const listAssetRevisions = (params = {}) => {
  const qs = new URLSearchParams()
  if (params.assetKind && params.assetKind !== 'ALL') qs.set('asset_kind', params.assetKind)
  if (params.revisionKind && params.revisionKind !== 'ALL') qs.set('revision_kind', params.revisionKind)
  if (params.agency && params.agency !== 'ALL') qs.set('agency', params.agency)
  if (params.regime && params.regime !== 'ALL') qs.set('regime', params.regime)
  if (params.humanOnly) qs.set('human_only', 'true')
  if (params.limit) qs.set('limit', String(params.limit))
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return api(`/api/assets/revisions${suffix}`)
}

export const exportRuleStudioHandoff = (params = {}) => {
  const qs = new URLSearchParams()
  if (params.agency && params.agency !== 'ALL') qs.set('agency', params.agency)
  if (params.regime && params.regime !== 'ALL') qs.set('regime', params.regime)
  if (params.status) qs.set('status', params.status)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return api(`/api/assets/rule-studio-export${suffix}`)
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
