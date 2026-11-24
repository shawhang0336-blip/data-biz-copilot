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
  STATIC_INDEXED: '待解析',
  PARSING: '解析中…',
  PARSED: '已提取实证',
  SKIPPED_AI: '已跳过/复用',
  FAILED: '解析异常',
}

const DOT_TONE_CLASS = {
  green: 'dot-green',
  amber: 'dot-amber',
  blue: 'dot-blue',
  purple: 'dot-blue',
  slate: 'dot-slate',
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

  const jobBadge = job ? formatBadge(BATCH_JOB_STATUS_LABELS, job.status) : null

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <h3 style={{ margin: 0, fontSize: 15.5 }}>批处理任务控制台</h3>
          {jobBadge && (
            <span className="status-inline">
              <span className={`dot ${DOT_TONE_CLASS[jobBadge.tone] || 'dot-slate'}`} />
              <span>{jobBadge.label}</span>
            </span>
          )}
        </div>
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
              {j.name}（{j.total_files} 份 · {formatBadge(BATCH_JOB_STATUS_LABELS, j.status).label}）
            </option>
          ))}
        </select>
      </div>

      {job && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, color: '#475569' }}>
              <span>进度：{job.processed_count} / {job.total_files}（{job.progress_pct}%）</span>
              <span className="tag tag-blue">预估节省调用 {job.estimated_ai_savings_pct}%</span>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {job.status === 'READY_REVIEW' && (
                <button type="button" className="btn btn-primary btn-sm" disabled={busyAction} onClick={handleStart}>
                  确认分层并启动解析
                </button>
              )}
              {job.status === 'RUNNING' && (
                <button type="button" className="btn btn-danger btn-sm" disabled={busyAction} onClick={handleCancel}>
                  中止任务
                </button>
              )}
              {(job.status === 'PAUSED_BUDGET' || job.status === 'CANCELLED' || job.status === 'FAILED') && (
                <button type="button" className="btn btn-primary btn-sm" disabled={busyAction} onClick={() => handleResume('pending_and_failed')}>
                  断点续跑剩余项
                </button>
              )}
              {job.failed_count > 0 && job.status !== 'RUNNING' && (
                <button type="button" className="btn btn-sm" disabled={busyAction} onClick={() => handleResume('failed_only')}>
                  仅重试失败项（{job.failed_count}）
                </button>
              )}
              {(job.status === 'COMPLETED' || job.completed_count > 0) && job.status !== 'RUNNING' && onSynthesize && (
                <button type="button" className="btn btn-sm" disabled={busyAction} onClick={onSynthesize}>
                  同步更新知识库
                </button>
              )}
            </div>
          </div>

          <div className="progress-track" style={{ marginTop: 10, marginBottom: 14 }}>
            <div className="progress-fill" style={{ width: `${job.progress_pct}%` }} />
          </div>

          <div className="stat-strip" style={{ marginBottom: 14 }}>
            <div className="stat-cell">
              <div className="stat-cell-label">核心 / 上游 / 跳过</div>
              <div className="stat-cell-value" style={{ fontSize: 17 }}>
                {job.tier1_count} / {job.tier2_count} / {job.tier3_count}
              </div>
            </div>
            <div className="stat-cell">
              <div className="stat-cell-label">完成 / 跳过 / 异常</div>
              <div className="stat-cell-value" style={{ fontSize: 17 }}>
                {job.completed_count} / {job.skipped_count} / {job.failed_count}
              </div>
            </div>
            <div className="stat-cell">
              <div className="stat-cell-label">模型消耗</div>
              <div className="stat-cell-value" style={{ fontSize: 17 }}>
                {job.llm_calls_used} 次 · {job.tokens_used.toLocaleString()}
              </div>
            </div>
            <div className="stat-cell">
              <div className="stat-cell-label">运行耗时与阶段</div>
              <div className="stat-cell-value" style={{ fontSize: 15 }}>
                {job.elapsed_seconds}s · {job.current_phase}
              </div>
            </div>
          </div>

          {job.error_summary && (
            <div className="disclosure-box" style={{ marginTop: 10 }}>{job.error_summary}</div>
          )}
          {panelError && <div className="alert-banner error">{panelError}</div>}

          <div className="toolbar-row">
            <select
              style={{ maxWidth: 220 }}
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
              style={{ maxWidth: 180 }}
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value)
                setPage(1)
              }}
            >
              <option value="">全部状态</option>
              <option value="STATIC_INDEXED">待解析</option>
              <option value="PARSING">解析中</option>
              <option value="PARSED">已提取实证</option>
              <option value="SKIPPED_AI">已跳过/复用</option>
              <option value="FAILED">仅看失败项</option>
            </select>
          </div>

          <div className="table-card" style={{ marginBottom: 0 }}>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>材料路径 / 名称</th>
                    <th style={{ width: 170 }}>解析分层</th>
                    <th style={{ width: 110 }}>状态</th>
                    {job.status !== 'RUNNING' && <th style={{ width: 190 }}>调整分层</th>}
                  </tr>
                </thead>
                <tbody>
                  {materials.map((m) => {
                    const tBadge = formatBadge(TIER_LABELS, m.tier)
                    return (
                      <tr key={m.id}>
                        <td>
                          <div className="table-title-main">
                            {m.material_kind === 'CODE_LOGIC' ? m.relative_path : m.title}
                          </div>
                          <div className="table-title-sub">
                            {m.error_message ? (
                              <span style={{ color: '#b91c1c' }}>{m.error_message}</span>
                            ) : (
                              m.skip_reason ? `${SKIP_REASON_LABELS[m.skip_reason] || m.skip_reason} · ${m.summary}` : m.summary
                            )}
                          </div>
                        </td>
                        <td>
                          <span className="tag">{tBadge.label}</span>
                        </td>
                        <td>
                          <span className="status-inline">
                            <span className={`dot ${m.status === 'FAILED' ? 'dot-red' : m.status === 'PARSED' ? 'dot-green' : 'dot-slate'}`} />
                            <span>{MATERIAL_STATUS_TEXT[m.status] || m.status}</span>
                          </span>
                        </td>
                        {job.status !== 'RUNNING' && (
                          <td>
                            {m.material_kind === 'CODE_LOGIC' ? (
                              <select
                                value={m.tier}
                                disabled={busyAction}
                                onChange={(e) => handleTierChange(m.id, e.target.value)}
                              >
                                <option value="TIER_1_CORE">核心报送深度解析</option>
                                <option value="TIER_2_UPSTREAM">上游精简并透传</option>
                                <option value="TIER_3_SKIP_AI">免模型跳过</option>
                              </select>
                            ) : (
                              <span style={{ fontSize: 12, color: '#94a3b8' }}>—</span>
                            )}
                          </td>
                        )}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {pagination.total_pages > 1 && (
              <div className="pagination-bar">
                <span>第 {pagination.page} / {pagination.total_pages} 页（共 {pagination.total} 条）</span>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                    上一页
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={page >= pagination.total_pages}
                    onClick={() => setPage((p) => Math.min(pagination.total_pages, p + 1))}
                  >
                    下一页
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
