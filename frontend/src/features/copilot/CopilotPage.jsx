import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getPanorama } from '../../api/catalog.js'
import { askCopilotStream, getCopilotSession, listCopilotSessions } from '../../api/copilot.js'
import {
  AlertIcon,
  ArrowRightIcon,
  ArrowUpRightIcon,
  BrandMark,
  CheckCircleIcon,
  ChevronDownIcon,
  ClockIcon,
  CloseIcon,
  PlusIcon,
  SendIcon,
  SettingsIcon,
  SidebarIcon,
} from '../../utils/icons.jsx'
import {
  RECONCILE_LABELS,
  TOOL_DISPLAY_LABELS,
  formatBadge,
} from '../../utils/labels.js'
import { MarkdownText } from '../../utils/markdown.jsx'
import EvidenceDrawer from './EvidenceDrawer.jsx'

const STARTER_PROMPTS = [
  {
    tag: '业务全景盘点',
    tone: 'blue',
    question: '我们的逻辑里面已经包含了哪些业务？有哪些监管要求还没落地？',
  },
  {
    tag: '剔除口径核对',
    tone: 'amber',
    question: '这些应报未报账户是什么情况？为什么要把它们从报送明细里剔除？',
  },
  {
    tag: '大额结售汇',
    tone: 'indigo',
    question: '机构大额结售汇和购汇资金来源、结汇资金使用是怎么处理的？交易对手怎么填？',
  },
  {
    tag: '个人存款分户账',
    tone: 'teal',
    question: '个人存款分户账为什么要剔除个体工商户和历史迁移账户？',
  },
  {
    tag: '对公客户信息',
    tone: 'blue',
    question: '对公客户信息里面的授信捞回和境内外名称怎么处理？',
  },
  {
    tag: '余额与对手口径',
    tone: 'indigo',
    question: '交易后余额和交易对手信息目前按什么口径处理，有没有内部口径不一致？',
  },
]

const DOT_TONE_CLASS = {
  green: 'dot-green',
  amber: 'dot-amber',
  blue: 'dot-blue',
  slate: 'dot-slate',
}

function stripLeadingDisclosures(content, disclosures) {
  let text = String(content || '').trim()
  for (const item of disclosures || []) {
    const marker = String(item?.text || '').trim()
    if (marker && text.startsWith(marker)) {
      text = text.slice(marker.length).trimStart()
    }
  }
  return text
}

function formatShortTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  if (sameDay) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

function summarizeTraceArgs(args) {
  if (!args || typeof args !== 'object') return ''
  if (args.asset_count) return `核对 ${args.asset_count} 项规则与口径`
  const picks = []
  for (const key of ['subject_theme', 'sub_domain', 'topic', 'metric', 'concept', 'role']) {
    const val = String(args[key] || '').trim()
    if (val) {
      picks.push(val.length > 26 ? `${val.slice(0, 26)}…` : val)
      if (picks.length >= 2) break
    }
  }
  return picks.join(' · ')
}

function buildStepsFromTrace(toolTrace) {
  if (!Array.isArray(toolTrace) || toolTrace.length === 0) return []
  return toolTrace.map((item, idx) => ({
    id: `trace-${idx}`,
    status: 'done',
    tool: item.tool,
    label: TOOL_DISPLAY_LABELS[item.tool] || '检索业务资产',
    detail: summarizeTraceArgs(item.args),
    result_count: item.result_count,
    disclose: Boolean(item.disclose),
  }))
}

function upsertStep(steps, incoming) {
  const next = [...steps]
  const idx = next.findIndex((s) => s.id === incoming.id)
  if (idx >= 0) {
    next[idx] = { ...next[idx], ...incoming }
  } else {
    next.push(incoming)
  }
  return next
}

export default function CopilotPage() {
  const [query, setQuery] = useState('')
  const [sessionId, setSessionId] = useState(null)
  const [sessions, setSessions] = useState([])
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 900 : true
  )
  const [kbStats, setKbStats] = useState({ loaded: false, capability_count: 0, rule_count: 0, caliber_count: 0 })
  const [activeEvidence, setActiveEvidence] = useState(null)
  const [expandedThinking, setExpandedThinking] = useState({})
  const [liveState, setLiveState] = useState(null)

  const bottomRef = useRef(null)
  const textareaRef = useRef(null)

  const reloadSessions = async () => {
    try {
      const list = await listCopilotSessions()
      setSessions(Array.isArray(list) ? list : [])
    } catch {
      // 忽略首次为空时的列表刷新错误
    }
  }

  useEffect(() => {
    reloadSessions()
    getPanorama()
      .then((res) => {
        const s = res?.stats || {}
        setKbStats({
          loaded: true,
          capability_count: s.capability_count || 0,
          rule_count: s.rule_count || 0,
          caliber_count: s.caliber_count || 0,
        })
      })
      .catch(() => {
        setKbStats((prev) => ({ ...prev, loaded: true }))
      })
  }, [])

  useEffect(() => {
    if (!busy || liveState?.phase !== 'thinking' || !liveState?.startedAt) return undefined
    const timer = setInterval(() => {
      setLiveState((prev) => {
        if (!prev || prev.phase !== 'thinking') return prev
        return {
          ...prev,
          thinkingSeconds: Math.floor((Date.now() - prev.startedAt) / 1000),
        }
      })
    }, 250)
    return () => clearInterval(timer)
  }, [busy, liveState?.phase, liveState?.startedAt])

  useEffect(() => {
    if (messages.length > 0 || busy) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [messages.length, busy, liveState?.steps?.length, liveState?.streamedAnswer?.length])

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const maxH = messages.length === 0 ? 150 : 110
    el.style.height = `${Math.min(el.scrollHeight, maxH)}px`
  }, [query, messages.length])

  const openSession = async (id) => {
    if (busy) return
    setError('')
    setLiveState(null)
    try {
      const detail = await getCopilotSession(id)
      setSessionId(detail.id)
      setMessages(detail.messages || [])
      setExpandedThinking({})
      if (typeof window !== 'undefined' && window.innerWidth < 900) {
        setSidebarOpen(false)
      }
    } catch (e) {
      setError(e.message)
    }
  }

  const startNewQuery = () => {
    if (busy) return
    setSessionId(null)
    setMessages([])
    setError('')
    setLiveState(null)
    setExpandedThinking({})
    setTimeout(() => textareaRef.current?.focus(), 20)
    if (typeof window !== 'undefined' && window.innerWidth < 900) {
      setSidebarOpen(false)
    }
  }

  const submitQuery = async (presetText) => {
    const q = (presetText ?? query).trim()
    if (!q || busy) return
    const startedAt = Date.now()
    let streamedSteps = []
    let frozenThinkingSeconds = null

    setBusy(true)
    setError('')
    setMessages((prev) => [...prev, { role: 'user', content: q }])
    if (!presetText) setQuery('')
    setLiveState({
      startedAt,
      thinkingSeconds: 0,
      phase: 'thinking',
      steps: [
        {
          id: 'plan',
          status: 'running',
          label: '理解问题意图，规划核查路径',
          detail: '',
        },
      ],
      disclosures: [],
      citedAssets: [],
      streamedAnswer: '',
    })

    try {
      const res = await askCopilotStream(
        { query: q, session_id: sessionId, role_lens: '业务' },
        (ev) => {
          if (ev.type === 'step') {
            streamedSteps = upsertStep(streamedSteps, ev)
            setLiveState((prev) => (prev ? { ...prev, steps: streamedSteps } : prev))
          } else if (ev.type === 'meta') {
            setLiveState((prev) =>
              prev
                ? {
                    ...prev,
                    disclosures: Array.isArray(ev.disclosures) ? ev.disclosures : [],
                    citedAssets: Array.isArray(ev.cited_assets) ? ev.cited_assets : [],
                  }
                : prev
            )
          } else if (ev.type === 'answer_delta') {
            if (frozenThinkingSeconds === null) {
              frozenThinkingSeconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000))
            }
            const deltaText = String(ev.delta || '')
            setLiveState((prev) =>
              prev
                ? {
                    ...prev,
                    phase: 'streaming',
                    thinkingSeconds: frozenThinkingSeconds,
                    streamedAnswer: prev.streamedAnswer + deltaText,
                  }
                : prev
            )
          }
        }
      )
      const finalSeconds = frozenThinkingSeconds ?? Math.max(1, Math.round((Date.now() - startedAt) / 1000))
      setSessionId(res.session_id)
      setLiveState(null)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          ...res,
          content: res.answer,
          liveSteps: streamedSteps,
          thinkingSeconds: finalSeconds,
        },
      ])
      await reloadSessions()
    } catch (e) {
      setLiveState(null)
      setError(e.message || '请求问答服务失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submitQuery()
    }
  }

  const entries = useMemo(() => {
    const out = []
    for (let i = 0; i < messages.length; i += 1) {
      const msg = messages[i]
      if (msg.role === 'user') {
        const nextMsg = messages[i + 1]
        if (nextMsg && nextMsg.role === 'assistant') {
          out.push({
            key: nextMsg.id || msg.id || `entry-${i}`,
            question: msg.content,
            assistant: nextMsg,
          })
          i += 1
        } else {
          out.push({
            key: msg.id || `entry-${i}`,
            question: msg.content,
            assistant: null,
          })
        }
      }
    }
    return out
  }, [messages])

  const activeSession = sessions.find((s) => s.id === sessionId)
  const hasKnowledge = (kbStats.rule_count || 0) > 0 || (kbStats.capability_count || 0) > 0
  const isHome = entries.length === 0

  return (
    <div className="qa-shell">
      {sidebarOpen && (
        <div
          className="sidebar-backdrop"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside className={`qa-sidebar ${sidebarOpen ? '' : 'collapsed'}`}>
        <div className="sidebar-top">
          <div className="sidebar-brand">
            <BrandMark size={28} />
            <span className="sidebar-brand-title">Data Biz Copilot</span>
          </div>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setSidebarOpen(false)}
            title="收起侧边栏"
            aria-label="收起侧边栏"
          >
            <SidebarIcon size={16} />
          </button>
        </div>

        <div className="new-query-wrap">
          <button
            type="button"
            className="btn new-query-btn"
            onClick={startNewQuery}
            disabled={busy}
          >
            <PlusIcon size={15} />
            <span>新查询</span>
          </button>
        </div>

        <div className="sidebar-section-label">最近查询</div>
        <div className="sidebar-history">
          {sessions.length === 0 ? (
            <div className="sidebar-empty">
              暂无历史查询记录。在右侧输入业务问题即可开始。
            </div>
          ) : (
            sessions.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`history-item ${sessionId === s.id ? 'active' : ''}`}
                onClick={() => openSession(s.id)}
              >
                <div className="history-item-title">{s.title}</div>
                <div className="history-item-meta">
                  <span>{formatShortTime(s.updated_at)}</span>
                </div>
              </button>
            ))
          )}
        </div>

        <div className="sidebar-footer">
          <Link to="/admin/sources" className="sidebar-admin-link">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <SettingsIcon size={16} />
              <span>平台管理</span>
            </span>
            <ArrowUpRightIcon size={15} />
          </Link>
        </div>
      </aside>

      <section className="qa-main">
        <header className="qa-topbar">
          <div className="qa-topbar-left">
            {!sidebarOpen && (
              <button
                type="button"
                className="icon-btn"
                onClick={() => setSidebarOpen(true)}
                title="展开侧边栏"
                aria-label="展开侧边栏"
              >
                <SidebarIcon size={17} />
              </button>
            )}
            <span className="qa-topbar-title">
              {activeSession ? activeSession.title : '业务口径与规则问答'}
            </span>
          </div>

          <div className="qa-topbar-right">
            <Link to="/admin/sources" className="btn btn-sm btn-ghost">
              <SettingsIcon size={14} />
              <span>平台管理</span>
            </Link>
          </div>
        </header>

        <div className="qa-scroll">
          {isHome ? (
            <div className="qa-home">
              <div className="qa-home-head">
                <div className="hero-brand-ring">
                  <BrandMark size={44} />
                </div>
                <h1 className="qa-home-title">Data Biz Copilot</h1>
                <p className="qa-home-sub">
                  查询监管报送规则、指标计算口径与制度对照差异
                </p>
                {kbStats.loaded && !hasKnowledge && (
                  <div className="home-empty-hint">
                    当前尚未导入业务材料，请先{' '}
                    <Link to="/admin/sources" className="link-btn">
                      前往平台管理导入材料 →
                    </Link>
                  </div>
                )}
              </div>

              <div className="query-box">
                <textarea
                  ref={textareaRef}
                  className="query-textarea"
                  rows={2}
                  placeholder="输入您想了解的业务规则、指标口径或制度差异问题…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={busy}
                />
                <div className="query-box-footer">
                  <div className="query-box-meta">
                    <span>Enter 查询 · Shift+Enter 换行</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-primary btn-cta"
                    onClick={() => submitQuery()}
                    disabled={busy || !query.trim()}
                  >
                    <SendIcon size={14} />
                    <span>查询</span>
                  </button>
                </div>
              </div>

              {error && (
                <div className="alert-banner error">
                  <span>{error}</span>
                  <button type="button" className="icon-btn" onClick={() => setError('')} aria-label="关闭">
                    <CloseIcon size={15} />
                  </button>
                </div>
              )}

              <div className="starter-section">
                <div className="starter-heading">常见业务问题</div>
                <div className="starter-grid">
                  {STARTER_PROMPTS.map((item) => (
                    <button
                      key={item.question}
                      type="button"
                      className="starter-row"
                      disabled={busy}
                      onClick={() => submitQuery(item.question)}
                    >
                      <div style={{ minWidth: 0 }}>
                        <span className={`starter-pill tone-${item.tone}`}>{item.tag}</span>
                        <span className="starter-row-text">{item.question}</span>
                      </div>
                      <ArrowUpRightIcon size={15} className="starter-arrow" />
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="qa-thread">
              {entries.map((entry, idx) => {
                const isLast = idx === entries.length - 1
                const isPending = isLast && !entry.assistant && busy
                const isThinkingNow = isPending && liveState?.phase === 'thinking'
                const isStreamingNow = isPending && liveState?.phase === 'streaming'

                const steps = isPending
                  ? (liveState?.steps || [])
                  : (entry.assistant?.liveSteps?.length
                      ? entry.assistant.liveSteps
                      : buildStepsFromTrace(entry.assistant?.tool_trace))

                const isExpanded = isThinkingNow ? true : Boolean(expandedThinking[entry.key])
                const secondsVal = isPending
                  ? liveState?.thinkingSeconds
                  : entry.assistant?.thinkingSeconds

                const disclosures = isPending
                  ? (liveState?.disclosures || [])
                  : (entry.assistant?.disclosures || [])

                const displayBody = isPending
                  ? (liveState?.streamedAnswer || '')
                  : stripLeadingDisclosures(entry.assistant?.content, disclosures)

                const citedAssets = entry.assistant?.cited_assets || []
                const followUps = entry.assistant?.follow_up_questions || []

                return (
                  <article key={entry.key} className="qa-entry">
                    <div className="qa-question-row">
                      <h2 className="qa-question-title">{entry.question}</h2>
                    </div>

                    {(isPending || steps.length > 0) && (
                      <div className={`thinking-card ${isThinkingNow ? 'running' : 'completed'}`}>
                        <button
                          type="button"
                          className="thinking-header"
                          onClick={() => {
                            if (!isThinkingNow) {
                              setExpandedThinking((prev) => ({
                                ...prev,
                                [entry.key]: !prev[entry.key],
                              }))
                            }
                          }}
                        >
                          <div className="thinking-header-left">
                            {isThinkingNow ? (
                              <span className="spin-ring" aria-hidden="true" />
                            ) : (
                              <CheckCircleIcon size={15} style={{ color: '#059669' }} />
                            )}
                            <span>
                              {isThinkingNow
                                ? '正在思考与核查业务依据…'
                                : `已完成 ${steps.length} 步思考与核查`}
                            </span>
                            {typeof secondsVal === 'number' && (
                              <span className="thinking-timer">
                                <ClockIcon size={12} />
                                <span>{isThinkingNow ? `已思考 ${secondsVal} 秒` : `用时 ${secondsVal} 秒`}</span>
                              </span>
                            )}
                          </div>
                          {!isThinkingNow && (
                            <div className="thinking-header-right">
                              <span>{isExpanded ? '收起过程' : '查看过程'}</span>
                              <ChevronDownIcon size={14} />
                            </div>
                          )}
                        </button>

                        {!isThinkingNow && !isExpanded && steps.length > 0 && (
                          <div className="thinking-preview">
                            {steps.map((s) => s.label).join(' → ')}
                          </div>
                        )}

                        {isExpanded && (
                          <div className="thinking-timeline">
                            {steps.map((step) => (
                              <div key={step.id} className="thinking-step-row">
                                <div className="thinking-step-left">
                                  <span className="thinking-step-icon">
                                    {step.status === 'running' ? (
                                      <span className="spin-ring" aria-hidden="true" />
                                    ) : (
                                      <CheckCircleIcon size={14} style={{ color: '#16a34a' }} />
                                    )}
                                  </span>
                                  <div>
                                    <span className="thinking-step-label">{step.label}</span>
                                    {step.detail && (
                                      <span className="thinking-step-detail">({step.detail})</span>
                                    )}
                                  </div>
                                </div>
                                <div className="thinking-step-right">
                                  {step.status === 'running' ? (
                                    <span className="step-pill running">处理中…</span>
                                  ) : typeof step.result_count === 'number' ? (
                                    <span className="step-pill">命中 {step.result_count} 项</span>
                                  ) : (
                                    <span className="step-pill">已完成</span>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {(isStreamingNow || entry.assistant) && (
                      <>
                        {disclosures.map((d, dIdx) => (
                          <div key={dIdx} className="disclosure-box">
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, marginBottom: 4 }}>
                              <AlertIcon size={15} />
                              <span>差异与缺口提示</span>
                            </div>
                            <div>{d.text}</div>
                          </div>
                        ))}

                        <div className={isStreamingNow ? 'streaming-body' : ''}>
                          <MarkdownText content={displayBody} />
                          {isStreamingNow && <span className="stream-cursor" aria-hidden="true" />}
                        </div>

                        {entry.assistant && citedAssets.length > 0 && (
                          <div className="source-section">
                            <div className="source-heading">参考依据</div>
                            <div className="source-chips">
                              {citedAssets.map((c, cIdx) => {
                                const badge = formatBadge(RECONCILE_LABELS, c.reconcile_status)
                                return (
                                  <button
                                    key={c.code}
                                    type="button"
                                    className="source-chip"
                                    onClick={() => setActiveEvidence(c)}
                                    title={`${c.title}（${badge.label}）`}
                                  >
                                    <span className="source-index">{cIdx + 1}</span>
                                    <span>{c.title}</span>
                                    <span className={`dot ${DOT_TONE_CLASS[badge.tone] || 'dot-slate'}`} />
                                  </button>
                                )
                              })}
                            </div>
                          </div>
                        )}

                        {entry.assistant && followUps.length > 0 && (
                          <div className="related-section">
                            <div className="source-heading">继续深入</div>
                            <div className="related-list">
                              {followUps.map((fq) => (
                                <button
                                  key={fq}
                                  type="button"
                                  className="related-item"
                                  onClick={() => submitQuery(fq)}
                                  disabled={busy}
                                >
                                  <span>{fq}</span>
                                  <ArrowRightIcon size={14} />
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                      </>
                    )}
                  </article>
                )
              })}

              {error && (
                <div className="alert-banner error">
                  <span>{error}</span>
                  <button type="button" className="icon-btn" onClick={() => setError('')} aria-label="关闭">
                    <CloseIcon size={15} />
                  </button>
                </div>
              )}

              <div ref={bottomRef} />
            </div>
          )}
        </div>

        {!isHome && (
          <div className="followup-dock">
            <div className="followup-bar">
              <textarea
                ref={textareaRef}
                className="followup-textarea"
                rows={1}
                placeholder="继续围绕当前业务问题追问…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={busy}
              />
              <button
                type="button"
                className="btn btn-primary btn-cta btn-sm"
                onClick={() => submitQuery()}
                disabled={busy || !query.trim()}
              >
                <SendIcon size={13} />
                <span>{busy ? '生成中…' : '追问'}</span>
              </button>
            </div>
          </div>
        )}
      </section>

      <EvidenceDrawer
        assetCode={activeEvidence?.code}
        assetPreview={activeEvidence}
        onClose={() => setActiveEvidence(null)}
      />
    </div>
  )
}
