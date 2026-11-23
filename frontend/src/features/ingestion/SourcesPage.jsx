import React, { useEffect, useState } from 'react'
import {
  listSources,
  loadRealSacPack,
  loadSamplePack,
  triggerSynthesis,
  uploadSource,
} from '../../api/ingestion.js'

export default function SourcesPage({ onSynthesized }) {
  const [sources, setSources] = useState([])
  const [kind, setKind] = useState('REGULATORY_DOC')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const reload = async () => {
    try {
      setSources(await listSources())
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
          接入监管/业务制度材料与任意方言 SQL 计算逻辑；底层物理对象将在后台静默解析为实证依据，对外仅产出纯业务解释资产。
        </p>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 12 }}>
          <button className="btn btn-primary" disabled={busy} onClick={handleLoadRealSac}>
            {busy ? '解析处理中…' : '一键载入真实 SAC 全量语料（25 脚本 + 16 监管/口径文档）'}
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
            <option value="REGULATORY_DOC">监管 / 业务制度文档 (md/docx/xlsx)</option>
            <option value="CODE_LOGIC">系统计算逻辑源 (仅作后台静默实证)</option>
          </select>
          <input type="file" multiple onChange={handleUpload} disabled={busy} style={{ maxWidth: 340 }} />
          <span style={{ fontSize: 12, color: '#64748b' }}>支持多选批量上传</span>
        </div>
        {message && <p style={{ color: '#047857', marginTop: 12, fontWeight: 600 }}>{message}</p>}
        {error && <p style={{ color: '#b91c1c', marginTop: 12 }}>{error}</p>}
      </div>

      <div className="card">
        <h3>
          已接入双源材料清单（共 {sources.length} 份 · 制度材料 {docCount} 份 · 后台静默逻辑源 {codeCount} 份）
        </h3>
        <div className="grid-2">
          {sources.map((s) => (
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
      </div>
    </div>
  )
}
