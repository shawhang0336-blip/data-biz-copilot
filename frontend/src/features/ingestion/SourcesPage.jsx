import React, { useEffect, useState } from 'react'
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
import BatchJobPanel from './BatchJobPanel.jsx'

export default function SourcesPage({ onSynthesized }) {
  const [sources, setSources] = useState([])
  const [jobs, setJobs] = useState([])
  const [activeJobId, setActiveJobId] = useState('')
  const [kind, setKind] = useState('REGULATORY_DOC')
  const [dirPath, setDirPath] = useState('')
  const [dedupTemplates, setDedupTemplates] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const reload = async () => {
    try {
      const [srcList, jobList] = await Promise.all([listSources(), listIngestionJobs()])
      setSources(srcList)
      setJobs(jobList)
      if (!activeJobId && jobList.length > 0) {
        setActiveJobId(jobList[0].id)
      }
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => { reload() }, [])

  const handleUpload = async (e) => {
    const files = Array.from(e.target.files || [])
    if (files.length === 0) return
    setBusy(true); setError(''); setMessage('')
    try {
      let okCount = 0
      let totalItems = 0
      for (const file of files) {
        const res = await uploadSource(kind, file)
        okCount += 1
        totalItems += res.item_count || 0
      }
      setMessage(`已批量接入 ${okCount} 份材料，共提取 ${totalItems} 条规范或底层逻辑实证。点击下方“重新碰撞对齐”即可生成或更新业务解释资产。`)
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
    setBusy(true); setError(''); setMessage('')
    try {
      const detail = await batchUploadSources({
        files,
        materialKind: 'AUTO',
        enableTemplateDedup: dedupTemplates,
        autoStart: false,
      })
      const job = detail.job
      setActiveJobId(job.id)
      setMessage(`已完成静态预扫描：共 ${job.total_files} 份材料（核心报送 ${job.tier1_count} 份 · 上游依赖 ${job.tier2_count} 份 · 免模型跳过 ${job.tier3_count} 份），请在下方确认分层后启动后台解析。`)
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
    setBusy(true); setError(''); setMessage('')
    try {
      const detail = await importDirectorySources({
        dirPath: dirPath.trim(),
        enableTemplateDedup: dedupTemplates,
        autoStart: false,
      })
      const job = detail.job
      setActiveJobId(job.id)
      setMessage(`目录预扫描完成：共识别 ${job.total_files} 份文件（预估节省模型调用 ${job.estimated_ai_savings_pct}%），可在下方微调分层或启动异步解析。`)
      await reload()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleLoadRealSac = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await loadRealSacPack()
      setMessage(`真实 SAC 全量语料解析融合完成：涵盖 ${res.capability_count} 个业务板块、${res.rule_count} 条处理规则、${res.caliber_count} 项指标口径。`)
      await reload()
      if (onSynthesized) onSynthesized(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleLoadSample = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await loadSamplePack()
      setMessage(`精简示例融合完成：识别 ${res.capability_count} 个业务板块、${res.rule_count} 条处理规则、${res.caliber_count} 项指标口径。`)
      await reload()
      if (onSynthesized) onSynthesized(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleSynthesize = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await triggerSynthesis()
      setMessage(`业务资产融合完成：${res.capability_count} 项业务板块 / ${res.rule_count} 条规则 / ${res.caliber_count} 项口径。`)
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

  return (
    <div>
      <div className="card">
        <h2>第一步：监管制度与系统逻辑双源接入</h2>
        <p style={{ color: '#475569', fontSize: 14 }}>
          接入监管/业务制度材料与系统计算逻辑；底层物理对象将在后台静默解析为实证依据，对外仅产出纯业务解释资产。
        </p>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 12 }}>
          <button className="btn btn-primary" disabled={busy} onClick={handleLoadRealSac}>
            {busy ? '解析处理中…' : '一键载入真实 SAC 全量语料（25 逻辑源 + 16 监管/口径文档）'}
          </button>
          <button className="btn" disabled={busy} onClick={handleLoadSample}>
            载入轻量演示样例
          </button>
          <button className="btn btn-success" disabled={busy} onClick={handleSynthesize}>
            重新碰撞对齐并生成业务解释
          </button>
        </div>
        <div style={{ marginTop: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ maxWidth: 260 }}>
            <option value="REGULATORY_DOC">监管 / 业务制度文档 (pdf/md/docx/xlsx)</option>
            <option value="CODE_LOGIC">系统计算逻辑源 (仅作后台静默实证)</option>
          </select>
          <input type="file" multiple onChange={handleUpload} disabled={busy} style={{ maxWidth: 340 }} />
          <span style={{ fontSize: 12, color: '#64748b' }}>适合单份/少量文件即时上传解析</span>
        </div>

        <div style={{ marginTop: 18, paddingTop: 16, borderTop: '1px solid #e2e8f0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
            <strong>大批量 / 千级逻辑工程导入（静态预扫描分层 + 异步断点续跑）</strong>
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, color: '#334155' }}>
              <input
                type="checkbox"
                checked={dedupTemplates}
                onChange={(e) => setDedupTemplates(e.target.checked)}
              />
              自动识别并复用同构分行/年度模板副本
            </label>
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginTop: 10 }}>
            <div>
              <label style={{ fontSize: 12, color: '#64748b', display: 'block', marginBottom: 4 }}>
                上传 ZIP 工程包或批量多文件
              </label>
              <input
                type="file"
                multiple
                accept=".zip,.sql,.prc,.pck,.bdy,.ddl,.hql,.md,.txt,.docx,.pdf,.xlsx,.csv"
                onChange={handleBatchFiles}
                disabled={busy}
                style={{ maxWidth: 300 }}
              />
            </div>
            <div>
              <label style={{ fontSize: 12, color: '#64748b', display: 'block', marginBottom: 4 }}>
                或选择整个本地目录（支持千级文件）
              </label>
              <input
                type="file"
                webkitdirectory=""
                directory=""
                multiple
                onChange={handleBatchFiles}
                disabled={busy}
                style={{ maxWidth: 300 }}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
            <input
              className="input"
              style={{ flex: 1, minWidth: 260 }}
              placeholder="或输入服务端代码仓目录绝对路径，执行零上传本地预扫描导入"
              value={dirPath}
              onChange={(e) => setDirPath(e.target.value)}
              disabled={busy}
            />
            <button className="btn btn-primary" disabled={busy} onClick={handleImportDir}>
              扫描本地目录并分级
            </button>
          </div>
        </div>

        {message && <p style={{ color: '#047857', marginTop: 12, fontWeight: 600 }}>{message}</p>}
        {error && <p style={{ color: '#b91c1c', marginTop: 12 }}>{error}</p>}
      </div>

      <BatchJobPanel
        jobs={jobs}
        activeJobId={activeJobId}
        onSelectJob={setActiveJobId}
        onJobUpdated={reload}
        onSynthesize={handleSynthesize}
      />

      <div className="card">
        <h3>
          已接入双源材料清单（共 {sources.length} 份 · 制度材料 {docCount} 份 · 后台静默逻辑源 {codeCount} 份）
        </h3>
        <div className="grid-2">
          {sources.slice(0, 60).map((s) => (
            <div key={s.id} className="card" style={{ marginBottom: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <strong>{s.material_kind === 'CODE_LOGIC' ? '底层逻辑实证源（细节已隔离封存）' : s.title}</strong>
                <span className={`badge ${s.material_kind === 'CODE_LOGIC' ? 'badge-blue' : 'badge-green'}`}>
                  {s.material_kind === 'CODE_LOGIC' ? '静默逻辑依据' : '业务制度材料'}
                </span>
              </div>
              <div style={{ fontSize: 13, color: '#475569' }}>{s.summary}</div>
            </div>
          ))}
        </div>
        {sources.length > 60 && (
          <p style={{ fontSize: 12, color: '#64748b', marginTop: 10 }}>
            当前共 {sources.length} 份材料（列表预览前 60 份，千级批量任务详情请在上方控制台分页查看）。
          </p>
        )}
      </div>
    </div>
  )
}
