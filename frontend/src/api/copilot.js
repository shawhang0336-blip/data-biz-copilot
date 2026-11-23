import { api } from './client.js'

export const listBusinessTools = () => api('/api/copilot/tools')
export const askCopilot = (payload) => api('/api/copilot/ask', {
  method: 'POST',
  body: JSON.stringify(payload),
})
export const listCopilotSessions = () => api('/api/copilot/sessions')
export const getCopilotSession = (sessionId) => api(`/api/copilot/sessions/${sessionId}`)
