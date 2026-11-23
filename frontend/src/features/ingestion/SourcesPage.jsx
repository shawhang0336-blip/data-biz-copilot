import React, { useEffect, useState } from 'react'
import { listSources, loadSamplePack, triggerSynthesis, uploadSource } from '../../api/ingestion.js'

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
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await uploadSource(kind, file)
      setMessage(`已接入「${res.title}」：${res.summary}`)
      await reload()
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
      setMessage(`双源样例融合完成：识别 ${res.capability_count} 个业务板块、${res.rule_count} 条处理规则、${res.caliber_count} 项指标口径。`)
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
      setMessage(`资产融合完成：${res.capability_count} 项业务版图 / ${res.rule_count} 条规则 / ${res.caliber_count} 项口径。`)
      await reload()
      if (onSynthesized) onSynthesized(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="card">
        <h2>第一步：监管制度与系统逻辑双源接入</h2>
        <p style={{ color: '#475569', fontSize: 14 }}>
          左侧接入监管制度/业务字典材料，右侧接入系统计算逻辑；底层代码细节将在后台静默封存为实证依据，对外只生成业务语言资产。
        </p>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 12 }}>
          <button className="btn btn-primary" disabled={busy} onClick={handleLoadSample}>
            {busy ? '处理中…' : '一键载入金融报送样例并生成资产'}
          </button>
          <button className="btn btn-success" disabled={busy} onClick={handleSynthesize}>
            重新碰撞对齐并合成业务资产
          </button>
        </div>
        <div style={{ marginTop: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ maxWidth: 240 }}>
            <option value="REGULATORY_DOC">监管 / 业务制度文档 (md/docx/xlsx)</option>
            <option value="CODE_LOGIC">系统计算逻辑源 (仅作后台实证)</option>
          </select>
          <input type="file" onChange={handleUpload} disabled={busy} style={{ maxWidth: 320 }} />
        </div>
        {message && <p style={{ color: '#047857', marginTop: 12, fontWeight: 600 }}>{message}</p>}
        {error && <p style={{ color: '#b91c1c', marginTop: 12 }}>{error}</p>}
      </div>

      <div className="card">
        <h3>已接入双源材料清单（共 {sources.length} 份）</h3>
        <div className="grid-2">
          {sources.map((s) => (
            <div key={s.id} className="card" style={{ marginBottom: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <strong>{s.material_kind === 'CODE_LOGIC' ? '底层逻辑实证源（已封存）' : s.title}</strong>
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
