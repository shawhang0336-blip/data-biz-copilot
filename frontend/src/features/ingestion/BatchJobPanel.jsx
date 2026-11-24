import React, { useEffect, useState } from 'react'
import {
  cancelIngestionJob,
  getIngestionJob,
  overrideMaterialTier,
  resumeIngestionJob,
  startIngestionJob,
} from '../../api/ingestion.js'
import {
  BATCH_JOB_STATUS_LABELS,
  SKIP_REASON_LABELS,
  TIER_LABELS,
  formatBadge,
} from '../../utils/labels.js'

const MATERIAL_STATUS_TEXT = {
  PENDING: '待扫描',
  STATIC_INDEXED: '已分层待解析',
  PARSING: '解析中…',
  PARSED: '已提取实证',
  SKIPPED_AI: '已免模型跳过/复用',
  FAILED: '解析异常',
}

export default function BatchJobPanel({
  jobs,
  activeJobId,
  onSelectJob,
  onJobUpdated,
  onSynthesize,
}) {
  const [detail, setDetail] = useState(null)
  const [tierFilter, setTierFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [busyAction, setBusyAction] = useState(false)
  const [panelError, setPanelError] = useState('')

  const loadDetail = async (jid = activeJobId, opts = {}) => {
    if (!jid) {
      setDetail(null)
      return
    }
    try {
      const res = await getIngestionJob(jid, {
        tier: opts.tier ?? tierFilter,
        status: opts.status ?? statusFilter,
        page: opts.page ?? page,
        pageSize: 20,
      })
      setDetail(res)
      setPanelError('')
    } catch (e) {
      setPanelError(e.message)
    }
  }

  useEffect(() => {
    loadDetail(activeJobId, { tier: tierFilter, status: statusFilter, page })
  }, [activeJobId, tierFilter, statusFilter, page])

  const jobStatus = detail?.job?.status
  useEffect(() => {
    if (!activeJobId || jobStatus !== 'RUNNING') return undefined
    const timer = setInterval(() => {
      loadDetail(activeJobId)
      if (onJobUpdated) onJobUpdated()
    }, 2000)
    return () => clearInterval(timer)
  }, [activeJobId, jobStatus])

  if (!jobs || jobs.length === 0) return null

  const job = detail?.job
  const materials = detail?.materials || []
  const pagination = detail?.pagination || { page: 1, total_pages: 1, total: 0 }

  const handleStart = async () => {
    if (!job) return
    setBusyAction(true)
    setPanelError('')
    try {
      await startIngestionJob(job.id)
      await loadDetail(job.id)
      if (onJobUpdated) onJobUpdated()
    } catch (e) {
      setPanelError(e.message)
    } finally {
      setBusyAction(false)
    }
  }

  const handleCancel = async () => {
    if (!job) return
    setBusyAction(true)
    setPanelError('')
    try {
      await cancelIngestionJob(job.id)
      await loadDetail(job.id)
      if (onJobUpdated) onJobUpdated()
    } catch (e) {
      setPanelError(e.message)
    } finally {
      setBusyAction(false)
    }
  }

  const handleResume = async (mode) => {
    if (!job) return
    setBusyAction(true)
    setPanelError('')
    try {
      await resumeIngestionJob(job.id, mode)
      await loadDetail(job.id)
      if (onJobUpdated) onJobUpdated()
    } catch (e) {
      setPanelError(e.message)
    } finally {
      setBusyAction(false)
    }
  }

  const handleTierChange = async (materialId, newTier) => {
    if (!job) return
    setBusyAction(true)
    setPanelError('')
    try {
      await overrideMaterialTier(job.id, materialId, newTier)
      await loadDetail(job.id)
      if (onJobUpdated) onJobUpdated()
    } catch (e) {
      setPanelError(e.message)
    } finally {
      setBusyAction(false)
    }
  }

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
        <h3 style={{ margin: 0 }}>大批量工程预扫描分流与断点续跑控制台</h3>
        <select
          style={{ maxWidth: 320 }}
          value={activeJobId || ''}
          onChange={(e) => {
            setPage(1)
            onSelectJob(e.target.value)
          }}
        >
          {jobs.map((j) => (
            <option key={j.id} value={j.id}>
              {j.name}（共 {j.total_files} 份 · {formatBadge(BATCH_JOB_STATUS_LABELS, j.status).label}）
            </option>
          ))}
        </select>
      </div>

      {job && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span className={`badge badge-${formatBadge(BATCH_JOB_STATUS_LABELS, job.status).tone}`}>
                {formatBadge(BATCH_JOB_STATUS_LABELS, job.status).label}
              </span>
              <span style={{ fontSize: 13, color: '#475569' }}>
                进度：{job.processed_count} / {job.total_files}（{job.progress_pct}%）
              </span>
              <span className="badge badge-teal">
                预估节省模型调用 {job.estimated_ai_savings_pct}%
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {job.status === 'READY_REVIEW' && (
                <button className="btn btn-primary" disabled={busyAction} onClick={handleStart}>
                  确认分层并启动后台异步解析
                </button>
              )}
              {job.status === 'RUNNING' && (
                <button className="btn btn-danger" disabled={busyAction} onClick={handleCancel}>
                  中止任务
                </button>
              )}
              {(job.status === 'PAUSED_BUDGET' || job.status === 'CANCELLED' || job.status === 'FAILED') && (
                <button className="btn btn-primary" disabled={busyAction} onClick={() => handleResume('pending_and_failed')}>
                  断点续跑剩余项
                </button>
              )}
              {job.failed_count > 0 && job.status !== 'RUNNING' && (
                <button className="btn" disabled={busyAction} onClick={() => handleResume('failed_only')}>
                  仅重试失败项（{job.failed_count}）
                </button>
              )}
              {(job.status === 'COMPLETED' || job.completed_count > 0) && job.status !== 'RUNNING' && onSynthesize && (
                <button className="btn btn-success" disabled={busyAction} onClick={onSynthesize}>
                  碰撞合成纯业务资产层
                </button>
              )}
            </div>
          </div>

          <div className="progress-track" style={{ marginTop: 10 }}>
            <div className="progress-fill" style={{ width: `${job.progress_pct}%` }} />
          </div>

          <div className="grid-4" style={{ marginTop: 12 }}>
            <div className="card" style={{ marginBottom: 0, padding: 12 }}>
              <div style={{ fontSize: 12, color: '#64748b' }}>核心报送 / 上游依赖 / 免模型跳过</div>
              <div style={{ fontSize: 15, fontWeight: 700, marginTop: 4 }}>
                {job.tier1_count} / {job.tier2_count} / {job.tier3_count}
              </div>
              <div style={{ fontSize: 12, color: '#475569', marginTop: 2 }}>
                含同构模板免费复用：{job.template_clone_count} 份
              </div>
            </div>
            <div className="card" style={{ marginBottom: 0, padding: 12 }}>
              <div style={{ fontSize: 12, color: '#64748b' }}>已完成 / 跳过复用 / 异常</div>
              <div style={{ fontSize: 15, fontWeight: 700, marginTop: 4 }}>
                {job.completed_count} / {job.skipped_count} / {job.failed_count}
              </div>
              <div style={{ fontSize: 12, color: '#475569', marginTop: 2 }}>
                已关联历史解析成果：{job.reused_count} 份
              </div>
            </div>
            <div className="card" style={{ marginBottom: 0, padding: 12 }}>
              <div style={{ fontSize: 12, color: '#64748b' }}>模型消耗与耗时</div>
              <div style={{ fontSize: 15, fontWeight: 700, marginTop: 4 }}>
                {job.llm_calls_used} 次 · {job.tokens_used.toLocaleString()} Tokens
              </div>
              <div style={{ fontSize: 12, color: '#475569', marginTop: 2 }}>
                已运行：{job.elapsed_seconds} 秒 · 阶段：{job.current_phase}
              </div>
            </div>
            <div className="card" style={{ marginBottom: 0, padding: 12 }}>
              <div style={{ fontSize: 12, color: '#64748b' }}>静态分流节省说明</div>
              <div style={{ fontSize: 12, color: '#334155', marginTop: 4 }}>
                {Object.keys(job.skip_reasons || {}).length === 0
                  ? '全部入选核心解析清单'
                  : Object.entries(job.skip_reasons)
                      .map(([k, v]) => `${SKIP_REASON_LABELS[k] || k} ${v} 份`)
                      .join(' · ')}
              </div>
            </div>
          </div>

          {job.error_summary && (
            <div className="disclosure-box" style={{ marginTop: 12 }}>{job.error_summary}</div>
          )}
          {panelError && <p style={{ color: '#b91c1c', marginTop: 8 }}>{panelError}</p>}

          <div className="filter-bar card" style={{ marginTop: 12, marginBottom: 10 }}>
            <strong style={{ fontSize: 13 }}>明细清单筛选：</strong>
            <select
              style={{ maxWidth: 240 }}
              value={tierFilter}
              onChange={(e) => {
                setTierFilter(e.target.value)
                setPage(1)
              }}
            >
              <option value="">全部分层</option>
              <option value="TIER_1_CORE">核心报送逻辑（深度解析）</option>
              <option value="TIER_2_UPSTREAM">上游加工依赖（精简并透传）</option>
              <option value="TIER_3_SKIP_AI">辅助或重复项（免模型跳过）</option>
            </select>
            <select
              style={{ maxWidth: 200 }}
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value)
                setPage(1)
              }}
            >
              <option value="">全部状态</option>
              <option value="STATIC_INDEXED">已分层待解析</option>
              <option value="PARSING">解析中</option>
              <option value="PARSED">已提取实证</option>
              <option value="SKIPPED_AI">已免模型跳过</option>
              <option value="FAILED">仅看失败项</option>
            </select>
            <span style={{ fontSize: 12, color: '#64748b' }}>
              第 {pagination.page} / {pagination.total_pages} 页（共 {pagination.total} 条）
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {materials.map((m) => {
              const tBadge = formatBadge(TIER_LABELS, m.tier)
              return (
                <div
                  key={m.id}
                  style={{
                    border: '1px solid #e2e8f0',
                    borderRadius: 8,
                    padding: '10px 12px',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: 10,
                    flexWrap: 'wrap',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 260 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <strong style={{ fontSize: 13 }}>
                        {m.material_kind === 'CODE_LOGIC' ? m.relative_path : m.title}
                      </strong>
                      <span className={`badge badge-${tBadge.tone}`}>{tBadge.label}</span>
                      <span className="badge badge-slate">{MATERIAL_STATUS_TEXT[m.status] || m.status}</span>
                    </div>
                    <div style={{ fontSize: 12, color: '#475569', marginTop: 4 }}>
                      {m.error_message ? (
                        <span style={{ color: '#b91c1c' }}>{m.error_message}</span>
                      ) : (
                        m.skip_reason ? `${SKIP_REASON_LABELS[m.skip_reason] || m.skip_reason} · ${m.summary}` : m.summary
                      )}
                    </div>
                  </div>
                  {m.material_kind === 'CODE_LOGIC' && job.status !== 'RUNNING' && (
                    <select
                      style={{ maxWidth: 210 }}
                      value={m.tier}
                      disabled={busyAction}
                      onChange={(e) => handleTierChange(m.id, e.target.value)}
                    >
                      <option value="TIER_1_CORE">调整为：核心报送深度解析</option>
                      <option value="TIER_2_UPSTREAM">调整为：上游精简并透传</option>
                      <option value="TIER_3_SKIP_AI">调整为：免模型跳过</option>
                    </select>
                  )}
                </div>
              )
            })}
          </div>

          {pagination.total_pages > 1 && (
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button className="btn" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                上一页
              </button>
              <button
                className="btn"
                disabled={page >= pagination.total_pages}
                onClick={() => setPage((p) => Math.min(pagination.total_pages, p + 1))}
              >
                下一页
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
