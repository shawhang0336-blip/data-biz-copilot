import { ApiError, api, formatApiErrorDetail } from './client.js'

export const listBusinessTools = () => api('/api/copilot/tools')
export const askCopilot = (payload) => api('/api/copilot/ask', {
  method: 'POST',
  body: JSON.stringify(payload),
})
export const listCopilotSessions = () => api('/api/copilot/sessions')
export const getCopilotSession = (sessionId) => api(`/api/copilot/sessions/${sessionId}`)

export async function askCopilotStream(payload, onEvent) {
  const response = await fetch('/api/copilot/ask/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const text = await response.text().catch(() => '')
    let detail = ''
    if (text) {
      try {
        const parsed = JSON.parse(text)
        detail = parsed?.detail ?? text.slice(0, 300)
      } catch {
        detail = text.slice(0, 300)
      }
    }
    throw new ApiError(response.status, detail || formatApiErrorDetail(null, response.status))
  }

  if (!response.body || typeof response.body.getReader !== 'function') {
    return askCopilot(payload)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finalResult = null

  const processChunk = (rawFrame) => {
    const lines = rawFrame.split('\n')
    for (const line of lines) {
      if (!line.startsWith('data:')) continue
      const jsonStr = line.slice(5).trim()
      if (!jsonStr) continue
      let ev = null
      try {
        ev = JSON.parse(jsonStr)
      } catch {
        continue
      }
      if (ev.type === 'step' || ev.type === 'meta' || ev.type === 'answer_delta') {
        if (onEvent) onEvent(ev)
      } else if (ev.type === 'result') {
        finalResult = ev.data
      } else if (ev.type === 'error') {
        throw new ApiError(500, ev.detail || '问答处理失败')
      }
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    let idx = buffer.indexOf('\n\n')
    while (idx !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      if (frame.trim()) processChunk(frame)
      idx = buffer.indexOf('\n\n')
    }
  }

  if (buffer.trim()) {
    processChunk(buffer)
  }

  if (!finalResult) {
    throw new ApiError(500, '未收到问答结果')
  }
  return finalResult
}
