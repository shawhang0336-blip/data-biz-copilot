import React, { useEffect, useState } from 'react'
import { askCopilot, getCopilotSession, listCopilotSessions } from '../../api/copilot.js'
import { RECONCILE_LABELS, formatBadge } from '../../utils/labels.js'
import { MarkdownText } from '../../utils/markdown.jsx'

const SUGGESTIONS = [
  '我们的逻辑里面已经包含了哪些业务？',
  '这些应报未报账户是什么情况？为什么要把它们从报送明细里剔除？',
  '机构大额结售汇和购汇资金来源、结汇资金使用是怎么处理的？交易对手怎么填？',
  '个人存款分户账为什么要剔除个体工商户和历史迁移账户？',
  '对公客户信息里面的授信捞回和境内外名称怎么处理？',
  '哪些制度要求是我们当前系统逻辑还没包含的？',
]

export default function CopilotPage() {
  const [query, setQuery] = useState('')
  const [roleLens, setRoleLens] = useState('业务')
  const [sessionId, setSessionId] = useState(null)
  const [sessions, setSessions] = useState([])
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const reloadSessions = async () => {
    try {
      setSessions(await listCopilotSessions())
    } catch {
      // 忽略首次为空时的列表刷新错误
    }
  }

  useEffect(() => { reloadSessions() }, [])

  const openSession = async (id) => {
    if (busy) return
    setError('')
    try {
      const detail = await getCopilotSession(id)
      setSessionId(detail.id)
      setRoleLens(detail.role_lens || '业务')
      setMessages(detail.messages || [])
    } catch (e) {
      setError(e.message)
    }
  }

  const startNewSession = () => {
    setSessionId(null)
    setMessages([])
    setError('')
  }

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
      await reloadSessions()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="copilot-layout">
      <aside className="card session-sidebar">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <strong style={{ fontSize: 14 }}>历史问答会话</strong>
          <button className="btn btn-primary" style={{ padding: '4px 10px', fontSize: 12 }} onClick={startNewSession}>
            + 新对话
          </button>
        </div>
        {sessions.length === 0 ? (
          <p style={{ fontSize: 12, color: '#64748b' }}>暂无历史会话，点击右侧问题立即体验。</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {sessions.map((s) => (
              <button
                key={s.id}
                className={`session-item ${sessionId === s.id ? 'active' : ''}`}
                onClick={() => openSession(s.id)}
              >
                <div style={{ fontWeight: 600, fontSize: 13 }}>{s.title}</div>
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{s.role_lens}视角</div>
              </button>
            ))}
          </div>
        )}
      </aside>

      <div>
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
            <div>
              <h2 style={{ margin: '0 0 4px' }}>第三步：Data Biz Copilot 业务逻辑问答台</h2>
              <p style={{ color: '#475569', fontSize: 13, margin: 0 }}>
                代码细节严格隔离——以系统真实逻辑与制度规范为依据，直接告诉你“现在系统是怎么做的、业务上怎么理解”。
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
              placeholder="输入您想了解的业务或逻辑问题，例如：对公客户信息里面的授信捞回怎么处理？"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && sendQuestion()}
            />
            <button className="btn btn-primary" onClick={() => sendQuestion()} disabled={busy} style={{ minWidth: 100 }}>
              {busy ? '解读中…' : '提问'}
            </button>
          </div>
          {error && <p style={{ color: '#b91c1c', marginTop: 10 }}>{error}</p>}
        </div>

        {messages.map((m, idx) => (
          <div key={idx} className="card" style={{ background: m.role === 'user' ? '#f8fafc' : '#fff' }}>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>
              {m.role === 'user' ? `提问（${roleLens}视角）` : 'Data Biz Copilot 纯业务解读'}
            </div>
            {(m.disclosures || []).map((d, i) => (
              <div key={i} className="disclosure-box">{d.text}</div>
            ))}
            {m.role === 'user' ? (
              <div style={{ lineHeight: 1.65, fontSize: 14 }}>{m.content}</div>
            ) : (
              <MarkdownText content={m.content} />
            )}
            {m.cited_assets && m.cited_assets.length > 0 && (
              <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid #e2e8f0' }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 6 }}>本次回答依托的业务资产依据：</div>
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
            {m.follow_up_questions && m.follow_up_questions.length > 0 && (
              <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {m.follow_up_questions.map((fq) => (
                  <button
                    key={fq}
                    className="btn"
                    style={{ fontSize: 12, padding: '4px 10px', color: '#1d4ed8' }}
                    onClick={() => sendQuestion(fq)}
                    disabled={busy}
                  >
                    追问：{fq}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
