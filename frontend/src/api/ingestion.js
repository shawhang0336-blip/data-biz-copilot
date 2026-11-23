import { api } from './client.js'

export const listSources = () => api('/api/sources')
export const loadSamplePack = () => api('/api/sources/load-sample', { method: 'POST' })
export const triggerSynthesis = () => api('/api/sources/synthesize', { method: 'POST' })
export const uploadSource = (materialKind, file) => {
  const form = new FormData()
  form.append('material_kind', materialKind)
  form.append('file', file)
  return api('/api/sources/upload', { method: 'POST', body: form })
}
