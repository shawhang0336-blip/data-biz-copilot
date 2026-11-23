import React, { useState } from 'react'
import { askCopilot } from '../../api/copilot.js'
import { RECONCILE_LABELS, formatBadge } from '../../utils/labels.js'

const SUGGESTIONS = [
  '我们的逻辑里面已经包含了哪些业务？',
  '大额外汇报送会纳入哪些客户，什么情况下会被自动剔除？',
  '什么叫应报未报账户？系统是怎么处理的？',
  '交易后余额和跨境联名账户目前分别按什么口径算？',
  '对照监管材料，有哪些规则存在分歧或系统目前还没包含？',
]

export default function CopilotPage() {
  const [query, setQuery] = useState('')
  const [roleLens, setRoleLens] = useState('业务')
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const sendQuestion = async (text) => {
    const q = (text ?? query).trim()
    if (!q || busy) return
    setBusy(true); setError('')
    setMessages((prev) => [...prev, { role: 'user', content: q }])
    if (!text) setQuery('')
    try {
      const res = await askCopilot({ query: q, session_id: sessionId, role_lens: roleLens })
      setSessionId(res.session_id)
      setMessages((prev) => [...prev, { role: 'assistant', ...res, content: res.answer }])
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <h2>第三步：Data Copilot 纯业务智能问答台</h2>
            <p style={{ color: '#475569', fontSize: 13, margin: 0 }}>
              代码细节全隔离——所有回答只讲业务逻辑、包含范围、指标口径与落地实证结论，绝不返回技术黑话。
            </p>
          </div>
          <select value={roleLens} onChange={(e) => setRoleLens(e.target.value)} style={{ maxWidth: 180 }}>
            <option value="业务">视角：业务人员</option>
            <option value="监管报送">视角：监管报送</option>
            <option value="合规">视角：合规审计</option>
            <option value="财务核算">视角：财务核算</option>
          </select>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
          {SUGGESTIONS.map((s) => (
            <button key={s} className="btn" onClick={() => sendQuestion(s)} disabled={busy}>{s}</button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
          <input
            className="input"
            placeholder="输入您关心的业务问题，例如：我们的逻辑里面已经包含了哪些业务？"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && sendQuestion()}
          />
          <button className="btn btn-primary" onClick={() => sendQuestion()} disabled={busy} style={{ minWidth: 100 }}>
            {busy ? '回答中…' : '提问'}
          </button>
        </div>
        {error && <p style={{ color: '#b91c1c', marginTop: 10 }}>{error}</p>}
      </div>

      {messages.map((m, idx) => (
        <div key={idx} className="card" style={{ background: m.role === 'user' ? '#f8fafc' : '#fff' }}>
          <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>
            {m.role === 'user' ? `提问（${roleLens}视角）` : 'Data Copilot 业务回答'}
          </div>
          {(m.disclosures || []).map((d, i) => (
            <div key={i} className="disclosure-box">{d.text}</div>
          ))}
          <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.65, fontSize: 14 }}>{m.content}</div>
          {m.cited_assets && m.cited_assets.length > 0 && (
            <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid #e2e8f0' }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 6 }}>引用业务资产依据：</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {m.cited_assets.map((c) => {
                  const badge = formatBadge(RECONCILE_LABELS, c.reconcile_status)
                  return (
                    <span key={c.code} className={`badge badge-${badge.tone}`}>
                      {c.title}（{badge.label}）
                    </span>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
