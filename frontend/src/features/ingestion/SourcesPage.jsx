import React, { useEffect, useMemo, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import {
  batchUploadSources,
  importDirectorySources,
  listIngestionJobs,
  listSources,
  loadRealSacPack,
  loadSamplePack,
  triggerSynthesis,
  uploadSource,
} from '../../api/ingestion.js'
import { FileTextIcon, FolderIcon, RefreshIcon, SearchIcon, UploadIcon } from '../../utils/icons.jsx'
import BatchJobPanel from './BatchJobPanel.jsx'

const PAGE_SIZE = 15

const SOURCE_STATUS_MAP = {
  PARSED: { label: '已解析', dot: 'dot-green' },
  SKIPPED_AI: { label: '已复用/跳过', dot: 'dot-slate' },
  STATIC_INDEXED: { label: '待解析', dot: 'dot-blue' },
  PARSING: { label: '解析中', dot: 'dot-blue' },
  PENDING: { label: '待处理', dot: 'dot-slate' },
  FAILED: { label: '解析异常', dot: 'dot-red' },
}

function formatDateShort(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function SourcesPage({ onSynthesized }) {
  const navigate = useNavigate()
  const outletContext = useOutletContext() || {}
  const { refreshStats } = outletContext

  const [sources, setSources] = useState([])
  const [jobs, setJobs] = useState([])
  const [activeJobId, setActiveJobId] = useState('')
  const [showImporter, setShowImporter] = useState(false)
  const [importMode, setImportMode] = useState('single')
  const [kind, setKind] = useState('REGULATORY_DOC')
  const [dirPath, setDirPath] = useState('')
  const [dedupTemplates, setDedupTemplates] = useState(true)
  const [sourceKindFilter, setSourceKindFilter] = useState('ALL')
  const [searchTerm, setSearchTerm] = useState('')
  const [page, setPage] = useState(1)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const reload = async () => {
    try {
      const [srcList, jobList] = await Promise.all([listSources(), listIngestionJobs()])
      setSources(srcList)
      setJobs(jobList)
      if (srcList.length === 0) {
        setShowImporter(true)
      }
      if (!activeJobId && jobList.length > 0) {
        setActiveJobId(jobList[0].id)
      }
      if (refreshStats) refreshStats()
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => {
    reload()
  }, [])

  const handleUpload = async (e) => {
    const files = Array.from(e.target.files || [])
    if (files.length === 0) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      let okCount = 0
      let totalItems = 0
      for (const file of files) {
        const res = await uploadSource(kind, file)
        okCount += 1
        totalItems += res.item_count || 0
      }
      setMessage(`已接入 ${okCount} 份材料，提取 ${totalItems} 条业务规范或逻辑实证；点击右上角“同步更新知识库”即可刷新问答底座。`)
      await reload()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
      e.target.value = ''
    }
  }

  const handleBatchFiles = async (e) => {
    const files = Array.from(e.target.files || [])
    if (files.length === 0) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const detail = await batchUploadSources({
        files,
        materialKind: 'AUTO',
        enableTemplateDedup: dedupTemplates,
        autoStart: false,
      })
      const job = detail.job
      setActiveJobId(job.id)
      setMessage(`已完成静态预扫描：共 ${job.total_files} 份材料（核心 ${job.tier1_count} · 上游 ${job.tier2_count} · 跳过 ${job.tier3_count}），请在下方确认后启动解析。`)
      await reload()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
      e.target.value = ''
    }
  }

  const handleImportDir = async () => {
    if (!dirPath.trim()) {
      setError('请输入本地代码仓或制度文件夹完整路径')
      return
    }
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const detail = await importDirectorySources({
        dirPath: dirPath.trim(),
        enableTemplateDedup: dedupTemplates,
        autoStart: false,
      })
      const job = detail.job
      setActiveJobId(job.id)
      setMessage(`目录扫描完成：共识别 ${job.total_files} 份文件（预估节省模型调用 ${job.estimated_ai_savings_pct}%）。`)
      await reload()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleLoadRealSac = async () => {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const res = await loadRealSacPack()
      setMessage(`已载入 SAC 全量语料：${res.capability_count} 个业务域 · ${res.rule_count} 条规则 · ${res.caliber_count} 项口径。`)
      await reload()
      if (onSynthesized) onSynthesized(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleLoadSample = async () => {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const res = await loadSamplePack()
      setMessage(`已载入轻量演示样例：${res.capability_count} 个业务域 · ${res.rule_count} 条规则 · ${res.caliber_count} 项口径。`)
      await reload()
      if (onSynthesized) onSynthesized(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleSynthesize = async () => {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const res = await triggerSynthesis()
      setMessage(`知识库已同步更新：${res.capability_count} 个业务域 · ${res.rule_count} 条规则 · ${res.caliber_count} 项口径。`)
      await reload()
      if (onSynthesized) onSynthesized(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const docCount = sources.filter((s) => s.material_kind === 'REGULATORY_DOC').length
  const codeCount = sources.filter((s) => s.material_kind === 'CODE_LOGIC').length

  const filteredSources = useMemo(() => {
    const q = searchTerm.trim().toLowerCase()
    return sources.filter((s) => {
      if (sourceKindFilter !== 'ALL' && s.material_kind !== sourceKindFilter) return false
      if (q) {
        const hay = `${s.title || ''} ${s.relative_path || ''} ${s.file_type || ''} ${s.summary || ''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [sources, sourceKindFilter, searchTerm])

  const totalPages = Math.max(1, Math.ceil(filteredSources.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const pagedSources = filteredSources.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">知识材料</h1>
          <p className="page-subtitle">
            管理监管制度文档与系统逻辑源材料，支撑业务资产提取与智能问答依据检索。
          </p>
        </div>
        <div className="header-actions">
          <button
            type="button"
            className="btn"
            onClick={() => setShowImporter((v) => !v)}
          >
            <UploadIcon size={14} />
            <span>{showImporter ? '收起导入面板' : '导入材料'}</span>
          </button>
          <button type="button" className="btn" disabled={busy} onClick={handleLoadRealSac}>
            载入 SAC 语料
          </button>
          <button type="button" className="btn" disabled={busy} onClick={handleLoadSample}>
            载入演示样例
          </button>
          <button type="button" className="btn" onClick={() => navigate('/admin/audit')}>
            审计中心 →
          </button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={handleSynthesize}>
            <RefreshIcon size={14} />
            <span>{busy ? '处理中…' : '同步更新知识库'}</span>
          </button>
        </div>
      </div>

      <div className="stat-strip">
        <div className="stat-cell">
          <div className="stat-cell-label">已接入材料总数</div>
          <div className="stat-cell-value">{sources.length}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-cell-label">监管制度文档</div>
          <div className="stat-cell-value">{docCount}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-cell-label">系统逻辑文件</div>
          <div className="stat-cell-value">{codeCount}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-cell-label">批处理任务</div>
          <div className="stat-cell-value">{jobs.length}</div>
        </div>
      </div>

      {message && <div className="alert-banner success" style={{ marginBottom: 14 }}>{message}</div>}
      {error && <div className="alert-banner error" style={{ marginBottom: 14 }}>{error}</div>}

      {showImporter && (
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
            <div className="underline-tabs" style={{ marginBottom: 0, borderBottom: 'none' }}>
              <button
                type="button"
                className={`underline-tab ${importMode === 'single' ? 'active' : ''}`}
                onClick={() => setImportMode('single')}
              >
                即时上传
              </button>
              <button
                type="button"
                className={`underline-tab ${importMode === 'batch' ? 'active' : ''}`}
                onClick={() => setImportMode('batch')}
              >
                批量工程（ZIP / 目录）
              </button>
              <button
                type="button"
                className={`underline-tab ${importMode === 'dir' ? 'active' : ''}`}
                onClick={() => setImportMode('dir')}
              >
                本地目录扫描
              </button>
            </div>

            {importMode !== 'single' && (
              <label style={{ fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 6, color: '#475569', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={dedupTemplates}
                  onChange={(e) => setDedupTemplates(e.target.checked)}
                />
                自动识别并复用同构模板副本
              </label>
            )}
          </div>

          <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid #e5e7eb' }}>
            {importMode === 'single' && (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#334155' }}>材料分类：</span>
                  <div className="segment-control" style={{ display: 'inline-flex' }}>
                    <button
                      type="button"
                      className={`segment-btn ${kind === 'REGULATORY_DOC' ? 'active' : ''}`}
                      onClick={() => setKind('REGULATORY_DOC')}
                    >
                      监管制度文档
                    </button>
                    <button
                      type="button"
                      className={`segment-btn ${kind === 'CODE_LOGIC' ? 'active' : ''}`}
                      onClick={() => setKind('CODE_LOGIC')}
                    >
                      系统逻辑文件
                    </button>
                  </div>
                </div>

                <label className="dropzone-label">
                  <input type="file" multiple className="sr-only" onChange={handleUpload} disabled={busy} />
                  <UploadIcon size={20} style={{ color: '#2563eb' }} />
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: '#0f172a' }}>
                    点击选择文件上传（支持多选）
                  </span>
                  <span style={{ fontSize: 12, color: '#64748b' }}>
                    支持 PDF / DOCX / DOC / XLSX / XLS / Markdown / TXT 制度文档及 SQL 逻辑脚本
                  </span>
                </label>
              </div>
            )}

            {importMode === 'batch' && (
              <div className="grid-2">
                <label className="dropzone-label">
                  <input
                    type="file"
                    multiple
                    accept=".zip,.sql,.prc,.pck,.bdy,.ddl,.hql,.md,.txt,.docx,.doc,.pdf,.xlsx,.xls,.csv"
                    className="sr-only"
                    onChange={handleBatchFiles}
                    disabled={busy}
                  />
                  <UploadIcon size={20} style={{ color: '#2563eb' }} />
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: '#0f172a' }}>
                    上传 ZIP 工程包或批量文件
                  </span>
                  <span style={{ fontSize: 12, color: '#64748b' }}>
                    自动静态预扫描并分级，支持千级文件断点续跑
                  </span>
                </label>

                <label className="dropzone-label">
                  <input
                    type="file"
                    webkitdirectory=""
                    directory=""
                    multiple
                    className="sr-only"
                    onChange={handleBatchFiles}
                    disabled={busy}
                  />
                  <FolderIcon size={20} style={{ color: '#2563eb' }} />
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: '#0f172a' }}>
                    选择整个本地文件夹
                  </span>
                  <span style={{ fontSize: 12, color: '#64748b' }}>
                    自动识别目录内制度文档与系统逻辑脚本
                  </span>
                </label>
              </div>
            )}

            {importMode === 'dir' && (
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                  className="input"
                  style={{ flex: 1, minWidth: 260 }}
                  placeholder="输入服务端代码仓或制度目录绝对路径，例如 /data/sac_repo"
                  value={dirPath}
                  onChange={(e) => setDirPath(e.target.value)}
                  disabled={busy}
                />
                <button type="button" className="btn btn-primary" disabled={busy} onClick={handleImportDir}>
                  扫描目录并分级
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      <BatchJobPanel
        jobs={jobs}
        activeJobId={activeJobId}
        onSelectJob={setActiveJobId}
        onJobUpdated={reload}
        onSynthesize={handleSynthesize}
      />

      <div className="toolbar-row">
        <div className="segment-control" style={{ display: 'inline-flex' }}>
          <button
            type="button"
            className={`segment-btn ${sourceKindFilter === 'ALL' ? 'active' : ''}`}
            onClick={() => {
              setSourceKindFilter('ALL')
              setPage(1)
            }}
          >
            全部 ({sources.length})
          </button>
          <button
            type="button"
            className={`segment-btn ${sourceKindFilter === 'REGULATORY_DOC' ? 'active' : ''}`}
            onClick={() => {
              setSourceKindFilter('REGULATORY_DOC')
              setPage(1)
            }}
          >
            制度文档 ({docCount})
          </button>
          <button
            type="button"
            className={`segment-btn ${sourceKindFilter === 'CODE_LOGIC' ? 'active' : ''}`}
            onClick={() => {
              setSourceKindFilter('CODE_LOGIC')
              setPage(1)
            }}
          >
            逻辑文件 ({codeCount})
          </button>
        </div>

        <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: 360 }}>
          <SearchIcon
            size={14}
            style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#94a3b8' }}
          />
          <input
            className="input"
            style={{ paddingLeft: 30 }}
            placeholder="按材料名称或摘要搜索…"
            value={searchTerm}
            onChange={(e) => {
              setSearchTerm(e.target.value)
              setPage(1)
            }}
          />
        </div>
      </div>

      <div className="table-card">
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>材料名称</th>
                <th style={{ width: 110 }}>分类</th>
                <th style={{ width: 100 }} className="num-col">提取条数</th>
                <th style={{ width: 110 }}>状态</th>
                <th style={{ width: 130 }}>接入时间</th>
              </tr>
            </thead>
            <tbody>
              {pagedSources.length === 0 ? (
                <tr>
                  <td colSpan={5} style={{ textAlign: 'center', padding: '28px 14px', color: '#64748b' }}>
                    暂无匹配的知识材料
                  </td>
                </tr>
              ) : (
                pagedSources.map((s) => {
                  const st = SOURCE_STATUS_MAP[s.status] || { label: s.status || '已接入', dot: 'dot-slate' }
                  const isDoc = s.material_kind === 'REGULATORY_DOC'
                  const displayName = s.title || s.relative_path || '未命名材料'
                  const ext = String(s.file_type || '').toUpperCase()
                  return (
                    <tr key={s.id}>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <FileTextIcon size={15} style={{ color: '#64748b', flexShrink: 0 }} />
                          <span className="table-title-main">{displayName}</span>
                          {ext && <span className="tag" style={{ fontSize: 11 }}>{ext}</span>}
                        </div>
                        {s.summary && (
                          <div className="table-title-sub" style={{ paddingLeft: 23 }}>{s.summary}</div>
                        )}
                      </td>
                      <td>
                        <span className={`tag ${isDoc ? 'tag-blue' : ''}`}>
                          {isDoc ? '制度文档' : '逻辑文件'}
                        </span>
                      </td>
                      <td className="num-col">{s.item_count ?? 0} 条</td>
                      <td>
                        <span className="status-inline">
                          <span className={`dot ${st.dot}`} />
                          <span>{st.label}</span>
                        </span>
                      </td>
                      <td style={{ color: '#64748b', fontSize: 12.5, whiteSpace: 'nowrap' }}>
                        {formatDateShort(s.created_at)}
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="pagination-bar">
          <span>
            共 {filteredSources.length} 份材料 · 第 {safePage} / {totalPages} 页
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              type="button"
              className="btn btn-sm"
              disabled={safePage <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              上一页
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={safePage >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              下一页
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
