import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getAssetBacking } from '../../api/catalog.js'
import {
  AlertIcon,
  ArrowUpRightIcon,
  CheckCircleIcon,
  CloseIcon,
} from '../../utils/icons.jsx'
import { RECONCILE_LABELS, STATUS_LABELS, formatBadge } from '../../utils/labels.js'

const ASSET_KIND_LABELS = {
  RULE: '处理规则',
  CALIBER: '指标口径',
  CAPABILITY: '业务板块',
}

const DOT_TONE_CLASS = {
  green: 'dot-green',
  amber: 'dot-amber',
  blue: 'dot-blue',
  slate: 'dot-slate',
}

export default function EvidenceDrawer({ assetCode, assetPreview, onClose }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!assetCode) {
      setData(null)
      setError('')
      return undefined
    }
    let active = true
    setLoading(true)
    setError('')
    getAssetBacking(assetCode)
      .then((res) => {
        if (active) setData(res)
      })
      .catch((err) => {
        if (active) setError(err.message || '加载依据详情失败')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [assetCode])

  useEffect(() => {
    if (!assetCode) return undefined
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [assetCode, onClose])

  if (!assetCode) return null

  const title = data?.title || assetPreview?.title || '业务资产依据详情'
  const assetKind = data?.asset_kind || assetPreview?.asset_kind
  const reconcileStatus = data?.reconcile_status || assetPreview?.reconcile_status
  const recBadge = formatBadge(RECONCILE_LABELS, reconcileStatus)
  const statusBadge = data?.status ? formatBadge(STATUS_LABELS, data.status) : null

  return (
    <div
      className="drawer-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="业务依据与制度对照"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <aside className="drawer-panel">
        <div className="drawer-header">
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
              <span className="tag tag-blue">
                {ASSET_KIND_LABELS[assetKind] || '业务资产'}
              </span>
              {reconcileStatus && (
                <span className="status-inline">
                  <span className={`dot ${DOT_TONE_CLASS[recBadge.tone] || 'dot-slate'}`} />
                  <span>{recBadge.label}</span>
                </span>
              )}
              {statusBadge && (
                <span className="tag">{statusBadge.label}</span>
              )}
            </div>
            <h3 style={{ margin: 0, fontSize: 16, lineHeight: 1.4 }}>{title}</h3>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭抽屉">
            <CloseIcon size={18} />
          </button>
        </div>

        <div className="drawer-body">
          {loading && (
            <div className="sub-card" style={{ color: '#64748b', fontSize: 13 }}>
              正在调取制度对照与系统实证…
            </div>
          )}

          {error && (
            <div className="alert-banner error">
              <span>{error}</span>
            </div>
          )}

          {data && (
            <>
              <div className="evidence-stat-box">
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, color: '#0f766e', fontWeight: 700, fontSize: 13.5 }}>
                  <CheckCircleIcon size={16} />
                  <span>系统运行实证：已关联 {data.logic_backing_count ?? 0} 处核验记录</span>
                </div>
                <p style={{ margin: '6px 0 0', fontSize: 12.5, color: '#115e59', lineHeight: 1.55 }}>
                  该业务口径已结合已收录的制度文档与系统实际运行规则完成交叉比对。
                </p>
              </div>

              {data.conflict_notes && (
                <div className="disclosure-box">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, marginBottom: 4 }}>
                    <AlertIcon size={15} />
                    <span>口径差异说明</span>
                  </div>
                  <div>{data.conflict_notes}</div>
                </div>
              )}

              {(data.processing_steps || []).length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: '#0f172a', marginBottom: 10 }}>
                    系统实际加工步骤（{data.processing_steps.length} 个核心环节）
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {data.processing_steps.map((st, idx) => (
                      <React.Fragment key={idx}>
                      {st.flow_label && (idx === 0 || data.processing_steps[idx - 1].flow_label !== st.flow_label) && (
                        <div className="process-flow-title">{st.flow_label}</div>
                      )}
                      <div className="process-step-card">
                        <div className="process-step-head">
                          <span className="process-step-badge">{st.order ? `第 ${st.order} 步` : `环节 ${idx + 1}`}{st.stage_role_label ? ` · ${st.stage_role_label}` : ''}</span>
                          <span style={{ fontWeight: 700, fontSize: 13, color: '#0f172a' }}>{st.stage}</span>
                        </div>
                        {st.flow_summary && (
                          <div style={{ fontSize: 12.5, color: '#334155', lineHeight: 1.6, marginTop: 6 }}>
                            {st.flow_summary}
                          </div>
                        )}
                        <div className="process-kv-list">
                          {(st.inclusions || []).map((inc, i) => (
                            <div key={`inc-${i}`} className="process-kv-row">
                              <span className="process-kv-key">纳入</span>
                              <span>{inc}</span>
                            </div>
                          ))}
                          {(st.exclusions || []).map((exc, i) => (
                            <div key={`exc-${i}`} className="process-kv-row">
                              <span className="process-kv-key warn">剔除</span>
                              <span>{exc}</span>
                            </div>
                          ))}
                          {(st.branches || []).map((br, i) => (
                            <div key={`br-${i}`} className="process-kv-row">
                              <span className="process-kv-key info">分支</span>
                              <span>{br}</span>
                            </div>
                          ))}
                          {st.calculation && (
                            <div className="process-kv-row">
                              <span className="process-kv-key">计算</span>
                              <span>{st.calculation}</span>
                            </div>
                          )}
                          {st.dedup && (
                            <div className="process-kv-row">
                              <span className="process-kv-key">去重</span>
                              <span>{st.dedup}</span>
                            </div>
                          )}
                        </div>
                      </div>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              )}

              <div style={{ marginTop: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#0f172a', marginBottom: 10 }}>
                  对应制度原文条款（{(data.clause_references || []).length} 条）
                </div>
                {(data.clause_references || []).length === 0 ? (
                  <div className="sub-card" style={{ fontSize: 13, color: '#64748b' }}>
                    暂无对应制度原文条款，该业务规则由系统实际处理逻辑梳理生成。
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {data.clause_references.map((cl, idx) => (
                      <div key={idx} className="sub-card">
                        <div style={{ fontSize: 12.5, fontWeight: 700, color: '#1e40af', marginBottom: 4 }}>
                          {cl.chapter ? `[${cl.chapter}] ` : ''}{cl.clause_title}
                        </div>
                        <div style={{ fontSize: 13, color: '#334155', lineHeight: 1.6 }}>
                          {cl.requirement}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        <div className="drawer-footer">
          <Link to="/admin/assets" className="btn btn-sm" onClick={onClose}>
            <span>在平台管理查看资产台账</span>
            <ArrowUpRightIcon size={14} />
          </Link>
          <button type="button" className="btn btn-sm btn-primary" onClick={onClose}>
            完成
          </button>
        </div>
      </aside>
    </div>
  )
}
