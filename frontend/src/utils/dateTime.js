const NAIVE_ISO_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/

/**
 * 后端统一以 UTC 存储时间戳；无时区后缀的 ISO 字符串自动补 Z 后按本地时区展示。
 */
export function parseBackendDate(iso) {
  if (!iso) return null
  if (iso instanceof Date) return Number.isNaN(iso.getTime()) ? null : iso
  const raw = String(iso).trim()
  if (!raw) return null
  const normalized = NAIVE_ISO_RE.test(raw) ? `${raw.replace(' ', 'T')}Z` : raw
  const d = new Date(normalized)
  return Number.isNaN(d.getTime()) ? null : d
}

export function formatTime(iso, options = {}) {
  if (!iso) return '—'
  try {
    const d = parseBackendDate(iso)
    if (!d) return iso
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      ...options,
    })
  } catch {
    return iso
  }
}
