import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAuditOverview, getAuditRunDetail } from '../../api/platform.js'
import CopyButton from '../../components/CopyButton.jsx'
import SelectDropdown from '../../components/SelectDropdown.jsx'
import { formatTime, parseBackendDate } from '../../utils/dateTime.js'

const RUN_KIND_OPTIONS = [
  { value: 'ALL', label: '全部链路类型', description: '批摄入、资产合成与单源解析全量账本' },
  { value: 'BATCH_INGEST', label: '千级工程批摄入', description: 'Phase 0 静态分流 + Wave 0~3 拓扑解析' },
  { value: 'ASSET_SYNTHESIS', label: '双源资产碰撞合成', description: '代码实证 × 监管条款融合与双维分类' },
]

const ANOMALY_OPTIONS = [
  { value: 'ALL', label: '全部批次状态' },
  { value: 'CHAIN_BROKEN', label: '仅哈希链异常' },
  { value: 'HAS_GUARD', label: '含双门禁拦截' },
  { value: 'HAS_PAUSE', label: '含预算暂停/断点续跑' },
  { value: 'FAILED', label: '失败或异常中止' },
]

const EVENT_FILTER_OPTIONS = [
  { value: 'ALL', label: '全部事件类型' },
  { value: 'GUARD', label: '仅门禁拦截事件' },
  { value: 'GOVERNANCE', label: '仅暂停/续跑/复核门禁' },
  { value: 'BROKEN', label: '仅验签异常事件' },
]

const NODE_LABELS = {
  created: '初始化运行账本',
  static_scan: 'Phase 0 静默扫描与血缘分层',
  tier_review_gate: '分层人工复核门禁',
  doc_wave: '制度文件先验解析波次',
  tier2_wave: 'Wave 1 上游逻辑精简解析',
  tier1_wave: 'Wave 2 核心逻辑深度解析',
  clone_wave: 'Wave 3 同构模板零成本克隆',
  budget_pause_gate: '预算阈值挂起恢复门',
  auto_synthesize: '自动重聚业务资产台账',
  finalize_batch: '批量入库封板完成',
  parse_sources: '加载条款与底层实证',
  align_evidence: '按业务域对齐与构草稿',
  refine_domain_worker: 'AI 领域规则与口径提纯',
  synthesize_assets: '纯业务资产落库发布',
}

const GUARD_META = {
  DE_JARGON: { label: '去技术化防泄露门禁', tone: 'red' },
  EVIDENCE_BACKING: { label: '实证白名单防幻觉门禁', tone: 'amber' },
  CONTRACT_GUARD: { label: '工具契约校验门禁', tone: 'blue' },
}

function shortHash(hash, head = 12) {
  if (!hash) return '—'
  const s = String(hash)
  return s.length <= head + 4 ? s : `${s.slice(0, head)}…${s.slice(-4)}`
}

function formatDurationMs(ms) {
  const n = Number(ms)
  if (!Number.isFinite(n) || n < 0) return '—'
  if (n === 0) return '<1ms'
  if (n < 1000) return `${Math.round(n)}ms`
  if (n < 60_000) return `${(n / 1000).toFixed(2)}s`
  return `${Math.floor(n / 60_000)}m ${((n % 60_000) / 1000).toFixed(1)}s`
}

function summarizeEvent(ev) {
  const p = ev.payload || {}
  switch (ev.kind) {
    case 'RUN_CREATED':
      return `创建运行账本（工作流：${p.spec_id || ev.node || 'workflow'}）`
    case 'BATCH_SCANNED':
      return `Phase 0 预扫描完成：共 ${p.total_files ?? 0} 份材料（核心 ${p.tier1_count ?? 0} · 上游 ${p.tier2_count ?? 0} · 跳过 ${p.tier3_count ?? 0}）`
    case 'TIER_REVIEW_REQUIRED':
      return '挂起等待管理员复核三层分流结果后启动解析'
    case 'BATCH_STARTED':
      return '启动四波次拓扑流式解析引擎'
    case 'BATCH_PAUSED_BUDGET':
      return `触达预算或账单保护上限，已安全挂起（可零重复续跑）：${p.reason || ''}`
    case 'RUN_RESUMED':
      return `从断点 Checkpoint 恢复执行${p.reason ? `（${p.reason}）` : ''}`
    case 'BATCH_COMPLETED':
      return '千级工程批摄入与实证落盘全部封板完成'
    case 'SOURCES_LOADED':
      return `加载 ${p.clause_count ?? 0} 条制度条款与 ${p.evidence_count ?? 0} 条底层实证`
    case 'EVIDENCE_ALIGNED':
      return `完成跨源业务域对齐，共识别 ${p.domain_count ?? 0} 个核心业务域`
    case 'ASSETS_SYNTHESIZED':
    case 'RUN_COMPLETED':
      return '纯业务资产卡生成并刷新万级 BM25 检索缓存'
    case 'BUS_REJECTED':
      return `Bus 物理门禁拦截工具 ${p.tool || 'unknown'}：${p.reason || '违反去技术化或实证白名单约束'}`
    case 'BATCH_CANCELLED':
    case 'RUN_CANCELLED':
      return `管理员主动中止执行：${p.reason || 'operator cancelled'}`
    case 'RUN_FAILED':
      return p.error || p.reason || '执行异常中断'
    default: {
      if (p.reason) return String(p.reason)
      if (p.message) return String(p.message)
      const keys = Object.keys(p)
      return keys.length ? `载荷字段：${keys.slice(0, 5).join('、')}` : '状态推进事件'
    }
  }
}

export default function AuditCenterPage() {
  const [params, setParams] = useSearchParams()
  const [runKind, setRunKind] = useState('ALL')
  const [anomalyFilter, setAnomalyFilter] = useState('ALL')
  const [searchText, setSearchText] = useState('')

  const [overview, setOverview] = useState(null)
  const [selectedRunId, setSelectedRunId] = useState(() => params.get('run') || '')
  const [detail, setDetail] = useState(null)

  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [reverifyBusy, setReverifyBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  const [activeTab, setActiveTab] = useState('hash-chain')
  const [eventFilter, setEventFilter] = useState('ALL')
  const [eventSearch, setEventSearch] = useState('')
  const [reverseOrder, setReverseOrder] = useState(false)
  const [expandedSeqs, setExpandedSeqs] = useState({})

  const reqRef = useRef(0)
  const urlRunId = params.get('run') || ''

  const loadDetail = useCallback(async (runId, { silent = false } = {}) => {
    if (!runId) {
      setDetail(null)
      return
    }
    const reqId = ++reqRef.current
    setSelectedRunId(runId)
    if (!silent) setDetailLoading(true)
    try {
      const payload = await getAuditRunDetail(runId)
      if (reqId !== reqRef.current) return
      setDetail(payload)
      if (!silent) setExpandedSeqs({})
    } catch (err) {
      if (reqId === reqRef.current) setError(err.message || '加载审计档案失败')
    } finally {
      if (reqId === reqRef.current && !silent) setDetailLoading(false)
    }
  }, [])

  const loadOverview = useCallback(async ({ preferredRunId = '', silent = false } = {}) => {
    if (!silent) setLoading(true)
    setError('')
    try {
      const data = await getAuditOverview({ runKind, limit: 120 })
      setOverview(data)
      const list = data?.runs || []
      const target =
        preferredRunId ||
        (list.some((r) => r.run_id === selectedRunId) ? selectedRunId : '') ||
        urlRunId ||
        list[0]?.run_id ||
        ''
      if (target) {
        await loadDetail(target, { silent })
      } else {
        setSelectedRunId('')
        setDetail(null)
      }
    } catch (err) {
      setError(err.message || '加载全链路审计总览失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [runKind, selectedRunId, urlRunId, loadDetail])

  useEffect(() => {
    loadOverview({ preferredRunId: urlRunId })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKind])

  useEffect(() => {
    if (urlRunId && urlRunId !== selectedRunId) {
      loadDetail(urlRunId)
    }
  }, [urlRunId, selectedRunId, loadDetail])

  async function handleReverify() {
    setReverifyBusy(true)
    setNotice('')
    try {
      await loadOverview({ preferredRunId: selectedRunId, silent: true })
      setNotice('已完成全部运行账本的 SHA-256 链式哈希实时重算验签。')
    } finally {
      setReverifyBusy(false)
    }
  }

  function handleSelectRun(runId) {
    const next = new URLSearchParams(params)
    next.set('run', runId)
    setParams(next, { replace: true })
    loadDetail(runId)
  }

  function handleExportEvidence() {
    if (!detail) return
    const payload = {
      exported_at: new Date().toISOString(),
      system: 'Data Biz Copilot Compliance Audit Center',
      audit_dossier: detail,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `copilot-audit-evidence-${detail.run_id.slice(0, 8)}.json`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    setNotice(`已导出批次 ${detail.run_id.slice(0, 8)} 的全链路合规审计证据 (.json)。`)
  }

  const summary = overview?.summary || {
    total_runs: 0,
    batch_runs: 0,
    synthesis_runs: 0,
    chain_valid_runs: 0,
    chain_broken_runs: 0,
    total_events: 0,
    total_bus_rejections: 0,
    de_jargon_rejections: 0,
    evidence_rejections: 0,
    total_pauses_and_resumes: 0,
    total_llm_calls: 0,
    total_tokens: 0,
    manual_governance_actions: 0,
    copilot_governance: { total_sessions: 0, total_answers: 0, disclosed_divergent: 0, disclosed_unrealized: 0 },
    verified_at: null,
  }

  const filteredRuns = useMemo(() => {
    const list = overview?.runs || []
    const q = searchText.trim().toLowerCase()
    return list.filter((r) => {
      if (anomalyFilter === 'CHAIN_BROKEN' && r.chain_valid) return false
      if (anomalyFilter === 'HAS_GUARD' && !(r.bus_rejected_count > 0)) return false
      if (anomalyFilter === 'HAS_PAUSE' && !(r.pause_resume_count > 0)) return false
      if (anomalyFilter === 'FAILED' && !['FAILED', 'CANCELLED', 'PAUSED_BUDGET'].includes(r.status)) return false
      if (!q) return true
      return `${r.run_id} ${r.title} ${r.kind_label} ${r.status}`.toLowerCase().includes(q)
    })
  }, [overview?.runs, anomalyFilter, searchText])

  const filteredEvents = useMemo(() => {
    const list = detail?.events || []
    const q = eventSearch.trim().toLowerCase()
    const matched = list.filter((ev) => {
      if (eventFilter === 'GUARD' && ev.kind !== 'BUS_REJECTED') return false
      if (
        eventFilter === 'GOVERNANCE' &&
        !['TIER_REVIEW_REQUIRED', 'BATCH_PAUSED_BUDGET', 'RUN_RESUMED', 'BATCH_CANCELLED', 'NODE_RETRY'].includes(ev.kind)
      ) {
        return false
      }
      if (eventFilter === 'BROKEN' && ev.chain_ok) return false
      if (!q) return true
      return `#${ev.seq} ${ev.kind} ${ev.node} ${ev.event_hash} ${summarizeEvent(ev)}`.toLowerCase().includes(q)
    })
    return reverseOrder ? [...matched].reverse() : matched
  }, [detail?.events, eventFilter, eventSearch, reverseOrder])

  const waterfallData = useMemo(() => {
    const trace = detail?.trace
    const spans = trace?.spans || []
    if (!spans.length) return { spans: [], totalMs: 0 }
    const startMs = parseBackendDate(trace.started_at)?.getTime() ?? 0
    const endMs = parseBackendDate(trace.ended_at)?.getTime() ?? startMs
    const totalMs = Math.max(Number(trace.total_duration_ms) || (endMs - startMs) || 0, 0)
    return {
      totalMs,
      spans: spans.map((sp, idx) => {
        const sStart = parseBackendDate(sp.started_at)?.getTime() ?? startMs
        const dur = Math.max(Number(sp.duration_ms) || 0, 0)
        const leftPct = totalMs > 0
          ? Math.min(Math.max(((sStart - startMs) / totalMs) * 100, 0), 96)
          : (idx / Math.max(spans.length, 1)) * 75
        const widthPct = totalMs > 0
          ? Math.min(Math.max((dur / totalMs) * 100, 2.4), 100 - leftPct)
          : 18
        return {
          ...sp,
          displayLabel: NODE_LABELS[sp.node] || sp.label || sp.node,
          leftPct,
          widthPct,
        }
      }),
    }
  }, [detail?.trace])

  const govCount = (detail?.bus_rejections?.length || 0) + (detail?.governance_actions?.length || 0)

  return (
    <div className="dbc-gov-page">
      <div className="dbc-page-header">
        <div>
          <span className="dbc-eyebrow">EventLog · SHA-256 防篡改事件账本与双门禁观测</span>
          <h1 className="dbc-page-title">全链路合规审计中心</h1>
          <p className="dbc-page-desc">
            实时重算千级工程批摄入与双源资产合成的 SHA-256 哈希链，集中核查去技术化门禁、实证白名单拦截、波次耗时瀑布与端到端加密指纹。
          </p>
        </div>
        <div className="dbc-header-actions">
          <button type="button" className="btn btn-sm" disabled={reverifyBusy || loading} onClick={handleReverify}>
            {reverifyBusy ? '正在重算验链…' : '重新验链'}
          </button>
          <button type="button" className="btn btn-sm btn-primary" disabled={!detail} onClick={handleExportEvidence}>
            导出审计证据 (.json)
          </button>
        </div>
      </div>

      {error && (
        <div className="dbc-alert is-error">
          <span>{error}</span>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setError('')}>关闭</button>
        </div>
      )}
      {notice && (
        <div className="dbc-alert is-ok">
          <span>{notice}</span>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setNotice('')}>关闭</button>
        </div>
      )}

      {/* 顶部 4 张审计 KPI 卡 */}
      <section className="dbc-kpi-grid">
        <article className={`dbc-kpi-card ${summary.chain_broken_runs > 0 ? 'is-danger' : 'is-safe'}`}>
          <div className="dbc-kpi-head">
            <span>哈希链实时验签</span>
            <span className={`dbc-pill ${summary.chain_broken_runs > 0 ? 'tone-red' : 'tone-green'}`}>
              {summary.chain_broken_runs > 0 ? `${summary.chain_broken_runs} 链断裂` : '全部完整'}
            </span>
          </div>
          <strong className="dbc-kpi-value">{summary.chain_valid_runs} / {summary.total_runs}</strong>
          <small className="dbc-kpi-sub">
            {summary.chain_broken_runs > 0
              ? `检测到 ${summary.chain_broken_runs} 个批次存在哈希篡改或断链`
              : `全量 SHA-256 连续不可变 · 验签于 ${formatTime(summary.verified_at)}`}
          </small>
        </article>

        <article className="dbc-kpi-card">
          <div className="dbc-kpi-head">
            <span>纳管不可变账本</span>
            <span className="dbc-pill tone-blue">{summary.total_events} 条事件</span>
          </div>
          <strong className="dbc-kpi-value">{summary.total_runs} 个运行批次</strong>
          <small className="dbc-kpi-sub">
            批量工程接入 {summary.batch_runs} 批 · 双源资产合成 {summary.synthesis_runs} 批
          </small>
        </article>

        <article className="dbc-kpi-card">
          <div className="dbc-kpi-head">
            <span>双门禁物理拦截</span>
            <span className={`dbc-pill ${summary.total_bus_rejections > 0 ? 'tone-amber' : 'tone-slate'}`}>
              {summary.total_bus_rejections > 0 ? '已拦截违规' : '零泄露'}
            </span>
          </div>
          <strong className="dbc-kpi-value">{summary.total_bus_rejections} 次拦截</strong>
          <small className="dbc-kpi-sub">
            去技术化防泄露 {summary.de_jargon_rejections} 次 · 实证白名单拦截 {summary.evidence_rejections} 次
          </small>
        </article>

        <article className="dbc-kpi-card">
          <div className="dbc-kpi-head">
            <span>问答披露 & 人工治理</span>
            <span className="dbc-pill tone-purple">Copilot 留痕</span>
          </div>
          <strong className="dbc-kpi-value">
            {summary.copilot_governance?.total_answers ?? 0} 次问答 / {summary.manual_governance_actions} 次修订确认
          </strong>
          <small className="dbc-kpi-sub">
            强制披露分歧 {summary.copilot_governance?.disclosed_divergent ?? 0} 次 · 未落地{' '}
            {summary.copilot_governance?.disclosed_unrealized ?? 0} 次 · LLM {summary.total_llm_calls} 次
          </small>
        </article>
      </section>

      <section className="dbc-audit-grid">
        {/* 左侧账本台账 */}
        <aside className="dbc-card dbc-ledger-panel">
          <div className="dbc-card-head">
            <div>
              <h2>运行账本台账 ({filteredRuns.length})</h2>
              <p>所有批处理接入与资产合成均受哈希链保护</p>
            </div>
          </div>

          <div className="dbc-ledger-filters">
            <input
              className="input"
              type="search"
              placeholder="搜索批次 ID、任务名称或状态…"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
            />
            <div className="dbc-filter-pair">
              <SelectDropdown ariaLabel="按运行类型筛选" size="sm" value={runKind} options={RUN_KIND_OPTIONS} onChange={setRunKind} />
              <SelectDropdown ariaLabel="按治理维度筛选" size="sm" value={anomalyFilter} options={ANOMALY_OPTIONS} onChange={setAnomalyFilter} />
            </div>
          </div>

          {loading ? (
            <div className="dbc-empty">正在实时重算全量哈希链…</div>
          ) : filteredRuns.length === 0 ? (
            <div className="dbc-empty">暂无符合条件的运行账本。前往「知识材料」载入样例或批量解析后将自动写入 EventLog。</div>
          ) : (
            <div className="dbc-ledger-list">
              {filteredRuns.map((run) => {
                const active = run.run_id === selectedRunId
                return (
                  <button
                    key={run.run_id}
                    type="button"
                    className={`dbc-ledger-item ${active ? 'active' : ''} ${!run.chain_valid ? 'is-broken' : ''}`}
                    onClick={() => handleSelectRun(run.run_id)}
                  >
                    <div className="dbc-ledger-top">
                      <span className={`dbc-pill ${run.run_kind === 'BATCH_INGEST' ? 'tone-purple' : 'tone-blue'}`}>
                        {run.kind_label}
                      </span>
                      <span className={`dbc-pill ${run.chain_valid ? 'tone-green' : 'tone-red'}`}>
                        {run.chain_valid ? '✓ 链完整' : `✕ 断链 #${run.broken_seq}`}
                      </span>
                    </div>
                    <strong className="dbc-ledger-title">{run.title}</strong>
                    <div className="dbc-ledger-meta">
                      <code>#{run.run_id.slice(0, 8)}</code>
                      <span>{run.event_count} 事件</span>
                      <span>状态：{run.status}</span>
                      {run.bus_rejected_count > 0 && <span className="dbc-danger-text">{run.bus_rejected_count} 拦截</span>}
                    </div>
                    <div className="dbc-ledger-foot">
                      <span>
                        {run.job_summary
                          ? `T1:${run.job_summary.tier1_count} / T2:${run.job_summary.tier2_count} / T3:${run.job_summary.tier3_count}`
                          : `LLM ${run.llm_calls} 次`}
                      </span>
                      <time>{formatTime(run.created_at)}</time>
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </aside>

        {/* 右侧 4-Tab 审计档案 */}
        <div className="dbc-card dbc-detail-panel">
          {detailLoading ? (
            <div className="dbc-empty">正在加载单批次 SHA-256 哈希链与耗时瀑布…</div>
          ) : !detail ? (
            <div className="dbc-empty">请从左侧台账选择任意运行批次，查看完整 SHA-256 哈希链、双门禁拦截与端到端指纹。</div>
          ) : (
            <>
              <div className={`dbc-verify-banner ${detail.chain_valid ? 'is-safe' : 'is-danger'}`}>
                <div className="dbc-verify-main">
                  <div className="dbc-verify-head">
                    <span className="dbc-pill tone-blue">{detail.kind_label}</span>
                    <h2>{detail.title}</h2>
                    <span className="dbc-pill tone-slate">{detail.status}</span>
                  </div>
                  <p>
                    {detail.chain_valid ? (
                      <>
                        <strong>SHA-256 哈希链验签通过</strong> · 共校验 <strong>{detail.events.length}</strong> 条连续不可变事件
                        （尾哈希 <code>{shortHash(detail.fingerprints?.event_chain?.tail_hash, 14)}</code>）。
                      </>
                    ) : (
                      <>
                        <strong>警告：检测到链断裂（seq #{detail.broken_seq}）</strong> · 存储哈希与实时重算 SHA-256 不一致！
                      </>
                    )}
                  </p>
                </div>
                <CopyButton text={detail.run_id} label="复制批次 ID" copiedLabel="已复制 ID" />
              </div>

              <div className="dbc-sub-tabs">
                <button type="button" className={activeTab === 'hash-chain' ? 'active' : ''} onClick={() => setActiveTab('hash-chain')}>
                  1. SHA-256 哈希链账本 ({detail.events.length})
                </button>
                <button type="button" className={activeTab === 'trace' ? 'active' : ''} onClick={() => setActiveTab('trace')}>
                  2. 波次与节点耗时瀑布 ({waterfallData.spans.length})
                </button>
                <button type="button" className={activeTab === 'guards' ? 'active' : ''} onClick={() => setActiveTab('guards')}>
                  3. 双门禁拦截与管控 ({govCount})
                </button>
                <button type="button" className={activeTab === 'fingerprints' ? 'active' : ''} onClick={() => setActiveTab('fingerprints')}>
                  4. 端到端指纹与证据导出
                </button>
              </div>

              {activeTab === 'hash-chain' && (
                <div className="dbc-tab-body">
                  <div className="dbc-inline-toolbar">
                    <input
                      className="input"
                      type="search"
                      placeholder="按 #seq、事件名、节点或哈希过滤…"
                      value={eventSearch}
                      onChange={(e) => setEventSearch(e.target.value)}
                    />
                    <SelectDropdown ariaLabel="筛选事件类型" size="sm" minWidth={165} value={eventFilter} options={EVENT_FILTER_OPTIONS} onChange={setEventFilter} />
                    <button type="button" className="btn btn-sm" onClick={() => setReverseOrder((v) => !v)}>
                      {reverseOrder ? '倒序（新→旧）' : '链式正序（创世→终态）'}
                    </button>
                  </div>

                  {!reverseOrder && eventFilter === 'ALL' && !eventSearch && (
                    <div className="dbc-genesis-row">
                      <span className="dbc-genesis-badge">GENESIS</span>
                      <code>{detail.fingerprints?.event_chain?.genesis_hash}</code>
                    </div>
                  )}

                  <ol className="dbc-chain-list">
                    {filteredEvents.map((ev) => {
                      const open = Boolean(expandedSeqs[ev.seq])
                      return (
                        <li key={ev.seq} className={`dbc-chain-item ${ev.chain_ok ? '' : 'is-broken'}`}>
                          <button
                            type="button"
                            className="dbc-chain-head"
                            onClick={() => setExpandedSeqs((prev) => ({ ...prev, [ev.seq]: !prev[ev.seq] }))}
                          >
                            <span className={`dbc-seq ${ev.chain_ok ? 'ok' : 'broken'}`}>
                              {ev.chain_ok ? '✓' : '✕'} #{ev.seq}
                            </span>
                            <span className="dbc-pill tone-blue">{ev.kind}</span>
                            <span className="dbc-node-chip">{NODE_LABELS[ev.node] || ev.node}</span>
                            <span className="dbc-event-summary">{summarizeEvent(ev)}</span>
                            <time className="dbc-event-time">{formatTime(ev.created_at)}</time>
                          </button>
                          <div className="dbc-hash-strip">
                            <span className="dbc-hash-chip"><em>prev</em> <code>{shortHash(ev.prev_hash, 10)}</code></span>
                            <span>→</span>
                            <span className="dbc-hash-chip is-self"><em>sha256</em> <code>{shortHash(ev.event_hash, 12)}</code></span>
                            <CopyButton text={ev.event_hash} label="复制哈希" className="btn btn-sm btn-ghost" />
                            {!ev.chain_ok && (
                              <span className="dbc-danger-text">
                                验签失败！期望：<code>{shortHash(ev.expected_hash, 12)}</code>
                              </span>
                            )}
                          </div>
                          {open && (
                            <pre className="dbc-json-pre">{JSON.stringify(ev.payload, null, 2)}</pre>
                          )}
                        </li>
                      )
                    })}
                  </ol>
                </div>
              )}

              {activeTab === 'trace' && (
                <div className="dbc-tab-body">
                  <div className="dbc-waterfall">
                    <div className="dbc-waterfall-head">
                      <span>执行阶段 / 波次节点</span>
                      <span>时间轴瀑布（总耗时 {formatDurationMs(waterfallData.totalMs)}）</span>
                      <span>耗时 / 事件段</span>
                    </div>
                    {waterfallData.spans.map((sp, idx) => (
                      <div className="dbc-waterfall-row" key={`${sp.node}-${sp.seq_start}-${idx}`}>
                        <div className="dbc-waterfall-node">
                          <strong>{sp.displayLabel}</strong>
                          <code>{sp.node}</code>
                        </div>
                        <div className="dbc-waterfall-track-wrap">
                          <div className="dbc-waterfall-track">
                            <div
                              className={`dbc-waterfall-bar tone-${sp.status === 'error' ? 'red' : sp.status === 'interrupted' ? 'amber' : 'green'}`}
                              style={{ left: `${sp.leftPct}%`, width: `${sp.widthPct}%` }}
                            />
                          </div>
                        </div>
                        <div className="dbc-waterfall-dur">
                          <strong>{formatDurationMs(sp.duration_ms)}</strong>
                          <small>#{sp.seq_start}–#{sp.seq_end} ({sp.event_count})</small>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {activeTab === 'guards' && (
                <div className="dbc-tab-body">
                  <h3 className="dbc-section-h3">Bus 双门禁物理拦截 ({detail.bus_rejections?.length || 0})</h3>
                  {!detail.bus_rejections?.length ? (
                    <div className="dbc-empty">本次运行零门禁拦截，全部 AI 输出均通过去技术化与实证白名单硬校验。</div>
                  ) : (
                    <div className="dbc-gov-list">
                      {detail.bus_rejections.map((item) => {
                        const gm = GUARD_META[item.guard_type] || GUARD_META.CONTRACT_GUARD
                        return (
                          <article className="dbc-gov-item is-danger" key={`rej-${item.seq}`}>
                            <div className="dbc-gov-item-top">
                              <span className="dbc-seq broken">#{item.seq}</span>
                              <span className={`dbc-pill tone-${gm.tone}`}>{gm.label}</span>
                              <code>{item.tool || 'tool'}</code>
                              <time>{formatTime(item.created_at)}</time>
                            </div>
                            <p>{item.reason}</p>
                          </article>
                        )
                      })}
                    </div>
                  )}

                  <h3 className="dbc-section-h3" style={{ marginTop: 14 }}>
                    预算暂停、分层复核与断点续跑 ({detail.governance_actions?.length || 0})
                  </h3>
                  {!detail.governance_actions?.length ? (
                    <div className="dbc-empty">本次运行未发生预算挂起或中断续跑。</div>
                  ) : (
                    <div className="dbc-gov-list">
                      {detail.governance_actions.map((item) => (
                        <article className="dbc-gov-item" key={`gov-${item.seq}`}>
                          <div className="dbc-gov-item-top">
                            <span className="dbc-seq ok">#{item.seq}</span>
                            <span className="dbc-pill tone-amber">{item.kind}</span>
                            <span className="dbc-node-chip">{NODE_LABELS[item.node] || item.node}</span>
                            <time>{formatTime(item.created_at)}</time>
                          </div>
                          <p>{item.summary}</p>
                        </article>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'fingerprints' && (
                <div className="dbc-tab-body">
                  <div className="dbc-fp-grid">
                    <article className="dbc-fp-card">
                      <span className="dbc-fp-step">环节 01 · 接入材料指纹</span>
                      <h3>知识材料 SHA-256 摘要 ({detail.fingerprints?.source_materials?.length || 0})</h3>
                      <ul className="dbc-fp-list">
                        {(detail.fingerprints?.source_materials || []).slice(0, 6).map((m, idx) => (
                          <li key={`${m.sha256}-${idx}`}>
                            <strong>{m.title}</strong>
                            <code>{shortHash(m.sha256, 16)}</code>
                          </li>
                        ))}
                      </ul>
                    </article>

                    <article className="dbc-fp-card">
                      <span className="dbc-fp-step">环节 02 · EventLog 链尾签名</span>
                      <h3>不可变哈希链锚点</h3>
                      <p>连续序号：#{detail.fingerprints?.event_chain?.first_seq ?? '—'} → #{detail.fingerprints?.event_chain?.last_seq ?? '—'}</p>
                      <div className="dbc-fp-hash-row">
                        <code>{detail.fingerprints?.event_chain?.tail_hash || '—'}</code>
                        <CopyButton text={detail.fingerprints?.event_chain?.tail_hash} label="复制" className="btn btn-sm btn-ghost" />
                      </div>
                    </article>

                    <article className="dbc-fp-card">
                      <span className="dbc-fp-step">环节 03 · 工程批分流统计</span>
                      <h3>Phase 0 三层分流与执行概览</h3>
                      {detail.fingerprints?.job_summary ? (
                        <p>
                          共 {detail.fingerprints.job_summary.total_files} 份 · 核心 {detail.fingerprints.job_summary.tier1_count} ·
                          上游 {detail.fingerprints.job_summary.tier2_count} · 跳过/克隆 {detail.fingerprints.job_summary.tier3_count}（克隆{' '}
                          {detail.fingerprints.job_summary.template_clone_count}）
                        </p>
                      ) : (
                        <p className="dbc-muted">该批次为双源资产合成或单文件解析运行。</p>
                      )}
                    </article>

                    <article className="dbc-fp-card">
                      <span className="dbc-fp-step">环节 04 · 业务资产治理现状</span>
                      <h3>纯业务资产与对账分布</h3>
                      <p>
                        业务版图 {detail.fingerprints?.asset_governance?.capabilities ?? 0} 个 · 处理规则{' '}
                        {detail.fingerprints?.asset_governance?.rules ?? 0} 条 · 指标口径{' '}
                        {detail.fingerprints?.asset_governance?.calibers ?? 0} 项 · 已人工确认{' '}
                        {detail.fingerprints?.asset_governance?.confirmed ?? 0} 项（分歧{' '}
                        {detail.fingerprints?.asset_governance?.divergent ?? 0} / 未落地{' '}
                        {detail.fingerprints?.asset_governance?.unrealized ?? 0}）
                      </p>
                    </article>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  )
}
