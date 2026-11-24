import { api } from './client.js'

export const listSources = () => api('/api/sources')
export const loadSamplePack = () => api('/api/sources/load-sample', { method: 'POST' })
export const loadRealSacPack = () => api('/api/sources/load-sac-real', { method: 'POST' })
export const triggerSynthesis = (targetDomains = null) =>
  targetDomains && targetDomains.length > 0
    ? api('/api/sources/synthesize', {
        method: 'POST',
        body: JSON.stringify({ target_domains: targetDomains }),
      })
    : api('/api/sources/synthesize', { method: 'POST' })

export const uploadSource = (materialKind, file) => {
  const form = new FormData()
  form.append('material_kind', materialKind)
  form.append('file', file)
  return api('/api/sources/upload', { method: 'POST', body: form })
}

export const batchUploadSources = ({
  files,
  materialKind = 'AUTO',
  jobName = '',
  enableTemplateDedup = true,
  autoStart = false,
  autoSynthesize = false,
}) => {
  const form = new FormData()
  form.append('material_kind', materialKind)
  form.append('job_name', jobName)
  form.append('enable_template_dedup', String(Boolean(enableTemplateDedup)))
  form.append('auto_start', String(Boolean(autoStart)))
  form.append('auto_synthesize', String(Boolean(autoSynthesize)))
  for (const file of files) {
    const name = file.webkitRelativePath || file.name || 'untitled'
    form.append('files', file, name)
  }
  return api('/api/sources/batch-upload', { method: 'POST', body: form })
}

export const importDirectorySources = ({
  dirPath,
  includeGlobs = [],
  excludeGlobs = [],
  jobName = '',
  enableTemplateDedup = true,
  autoStart = false,
  autoSynthesize = false,
}) =>
  api('/api/sources/import-dir', {
    method: 'POST',
    body: JSON.stringify({
      dir_path: dirPath,
      include_globs: includeGlobs,
      exclude_globs: excludeGlobs,
      job_name: jobName,
      enable_template_dedup: enableTemplateDedup,
      auto_start: autoStart,
      auto_synthesize: autoSynthesize,
    }),
  })

export const listIngestionJobs = () => api('/api/sources/jobs')

export const getIngestionJob = (jobId, { tier = '', status = '', page = 1, pageSize = 50 } = {}) => {
  const params = new URLSearchParams()
  if (tier) params.set('tier', tier)
  if (status) params.set('status', status)
  params.set('page', String(page))
  params.set('page_size', String(pageSize))
  return api(`/api/sources/jobs/${jobId}?${params.toString()}`)
}

export const overrideMaterialTier = (jobId, materialId, tier) =>
  api(`/api/sources/jobs/${jobId}/materials/${materialId}/tier`, {
    method: 'PATCH',
    body: JSON.stringify({ tier }),
  })

export const startIngestionJob = (jobId) =>
  api(`/api/sources/jobs/${jobId}/start`, { method: 'POST' })

export const cancelIngestionJob = (jobId) =>
  api(`/api/sources/jobs/${jobId}/cancel`, { method: 'POST' })

export const resumeIngestionJob = (jobId, mode = 'pending_and_failed') =>
  api(`/api/sources/jobs/${jobId}/resume`, {
    method: 'POST',
    body: JSON.stringify({ mode }),
  })
