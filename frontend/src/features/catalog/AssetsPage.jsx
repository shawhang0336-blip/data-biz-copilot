import React, { useEffect, useState } from 'react'
import {
  confirmCaliber,
  confirmRule,
  editCaliber,
  editRule,
  getAssetBacking,
  getPanorama,
} from '../../api/catalog.js'
import {
  COVERAGE_LABELS,
  RECONCILE_LABELS,
  RULE_CATEGORY_LABELS,
  STATUS_LABELS,
  formatBadge,
} from '../../utils/labels.js'

export default function AssetsPage() {
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('capabilities')
  const [editing, setEditing] = useState(null)
  const [backing, setBacking] = useState(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const reload = async () => {
    try {
      setData(await getPanorama())
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => { reload() }, [])

  const handleConfirmRule = async (rule) => {
    setError(''); setNote('')
    try {
      await confirmRule(rule.id, { expected_version: rule.current_version, confirmer: '业务负责人', comment: '页面核对确认' })
      setNote(`已签字确认业务规则「${rule.title}」`)
      await reload()
    } catch (e) {
      setError(e.message)
    }
  }

  const handleConfirmCaliber = async (cal) => {
    setError(''); setNote('')
    try {
      await confirmCaliber(cal.id, { expected_version: cal.current_version, confirmer: '业务负责人', comment: '口径确认' })
      setNote(`已签字确认业务口径「${cal.metric_name}」`)
      await reload()
    } catch (e) {
      setError(e.message)
    }
  }

  const handleSaveEdit = async () => {
    if (!editing) return
    setError(''); setNote('')
    try {
      if (editing.kind === 'RULE') {
        await editRule(editing.row.id, {
          expected_version: editing.row.current_version,
          editor: '业务专员',
          reason: '优化业务表达',
          changes: editing.changes,
        })
      } else {
        await editCaliber(editing.row.id, {
          expected_version: editing.row.current_version,
          editor: '业务专员',
          reason: '优化口径说明',
          changes: editing.changes,
        })
      }
      setEditing(null)
      setNote('业务表述已保存（如含技术黑话会被门禁拦截）')
      await reload()
    } catch (e) {
      setError(e.message)
    }
  }

  const inspectBacking = async (code) => {
    try {
      setBacking(await getAssetBacking(code))
    } catch (e) {
      setError(e.message)
    }
  }

  const stats = data?.stats || {}

  return (
    <div>
      <div className="card">
        <h2>第二步：纯业务资产全景与签字中心</h2>
        <div className="grid-4" style={{ marginTop: 12 }}>
          <div className="card" style={{ marginBottom: 0 }}><strong>{stats.capability_count || 0}</strong><div>已识别业务板块</div></div>
          <div className="card" style={{ marginBottom: 0 }}><strong>{stats.rule_count || 0}</strong><div>业务纳入/剔除/分支规则</div></div>
          <div className="card" style={{ marginBottom: 0 }}><strong>{stats.caliber_count || 0}</strong><div>业务指标口径</div></div>
          <div className="card" style={{ marginBottom: 0 }}><strong>{stats.confirmed_count || 0}</strong><div>已签字确认资产</div></div>
        </div>
        {note && <p style={{ color: '#047857', marginTop: 10 }}>{note}</p>}
        {error && <p style={{ color: '#b91c1c', marginTop: 10 }}>{error}</p>}
      </div>

      <div className="tabs">
        <button className={`tab-btn ${tab === 'capabilities' ? 'active' : ''}`} onClick={() => setTab('capabilities')}>1. 业务版图（逻辑包含了哪些业务）</button>
        <button className={`tab-btn ${tab === 'rules' ? 'active' : ''}`} onClick={() => setTab('rules')}>2. 业务处理规则（纳入/剔除/分支/防重）</button>
        <button className={`tab-btn ${tab === 'calibers' ? 'active' : ''}`} onClick={() => setTab('calibers')}>3. 业务指标口径卡</button>
        <button className={`tab-btn ${tab === 'reconcile' ? 'active' : ''}`} onClick={() => setTab('reconcile')}>4. 制度 vs 逻辑落地对账</button>
      </div>

      {tab === 'capabilities' && (
        <div className="grid-2">
          {(data?.capabilities || []).map((c) => {
            const cov = formatBadge(COVERAGE_LABELS, c.coverage_status)
            return (
              <div key={c.id} className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h3>{c.name}</h3>
                  <span className={`badge badge-${cov.tone}`}>{cov.label}</span>
                </div>
                <p style={{ fontSize: 14, color: '#334155' }}>{c.description}</p>
                <p style={{ fontSize: 13, color: '#475569' }}><strong>包含范围：</strong>{c.scope_summary}</p>
                <div style={{ fontSize: 12, color: '#64748b' }}>
                  规则 {c.rule_count} 条 · 口径 {c.caliber_count} 项 · 底层逻辑实证 {c.logic_backing_count} 处 · 面向：{c.audience}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {tab === 'rules' && (data?.rules || []).map((r) => {
        const rec = formatBadge(RECONCILE_LABELS, r.reconcile_status)
        const st = formatBadge(STATUS_LABELS, r.status)
        return (
          <div key={r.id} className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <span className="badge badge-blue" style={{ marginRight: 8 }}>{RULE_CATEGORY_LABELS[r.rule_category] || r.rule_category}</span>
                <strong>{r.title}</strong>
                <span style={{ marginLeft: 8, fontSize: 12, color: '#64748b' }}>（{r.capability_name}）</span>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span className={`badge badge-${rec.tone}`}>{rec.label}</span>
                <span className={`badge badge-${st.tone}`}>{st.label}</span>
              </div>
            </div>
            <p style={{ fontSize: 14, marginTop: 10 }}><strong>业务条件：</strong>{r.business_condition}</p>
            <p style={{ fontSize: 14 }}><strong>处理结论：</strong>{r.business_action}</p>
            <p style={{ fontSize: 13, color: '#475569' }}><strong>业务原因：</strong>{r.business_rationale}</p>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button className="btn" onClick={() => setEditing({ kind: 'RULE', row: r, changes: { business_condition: r.business_condition, business_action: r.business_action } })}>修订业务表述</button>
              <button className="btn btn-success" onClick={() => handleConfirmRule(r)}>签字确认 ({`v${r.current_version}`})</button>
              <button className="btn" onClick={() => inspectBacking(r.code)}>查看业务依据</button>
            </div>
          </div>
        )
      })}

      {tab === 'calibers' && (data?.calibers || []).map((c) => {
        const rec = formatBadge(RECONCILE_LABELS, c.reconcile_status)
        const st = formatBadge(STATUS_LABELS, c.status)
        return (
          <div key={c.id} className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <strong>{c.metric_name}</strong>
              <div style={{ display: 'flex', gap: 8 }}>
                <span className={`badge badge-${rec.tone}`}>{rec.label}</span>
                <span className={`badge badge-${st.tone}`}>{st.label}</span>
              </div>
            </div>
            <p style={{ fontSize: 14, marginTop: 8 }}><strong>业务定义：</strong>{c.business_definition}</p>
            <p style={{ fontSize: 14 }}><strong>系统计算口径：</strong>{c.formula_summary}</p>
            <p style={{ fontSize: 13, color: '#475569' }}><strong>防混淆提醒：</strong>{c.distinction_notes}</p>
            {c.conflict_notes && <div className="disclosure-box">{c.conflict_notes}</div>}
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button className="btn" onClick={() => setEditing({ kind: 'CALIBER', row: c, changes: { formula_summary: c.formula_summary, distinction_notes: c.distinction_notes } })}>修订口径</button>
              <button className="btn btn-success" onClick={() => handleConfirmCaliber(c)}>签字确认 ({`v${c.current_version}`})</button>
              <button className="btn" onClick={() => inspectBacking(c.code)}>查看依据</button>
            </div>
          </div>
        )
      })}

      {tab === 'reconcile' && (
        <div className="card">
          {(data?.reconcile?.items || []).map((item) => {
            const rec = formatBadge(RECONCILE_LABELS, item.reconcile_status)
            return (
              <div key={item.code} style={{ padding: '10px 0', borderBottom: '1px solid #e2e8f0', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                <div>
                  <strong>{item.name}</strong> <span style={{ color: '#64748b', fontSize: 12 }}>({item.capability_name})</span>
                  <div style={{ fontSize: 13, color: '#475569' }}>{item.summary}</div>
                </div>
                <span className={`badge badge-${rec.tone}`} style={{ alignSelf: 'center' }}>{rec.label}</span>
              </div>
            )
          })}
        </div>
      )}

      {editing && (
        <div className="card" style={{ borderColor: '#2563eb' }}>
          <h3>修订纯业务表述（禁止输入代码或表名）</h3>
          {Object.entries(editing.changes).map(([k, v]) => (
            <div key={k} style={{ marginBottom: 10 }}>
              <textarea rows={2} value={v} onChange={(e) => setEditing({ ...editing, changes: { ...editing.changes, [k]: e.target.value } })} />
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary" onClick={handleSaveEdit}>保存修订</button>
            <button className="btn" onClick={() => setEditing(null)}>取消</button>
          </div>
        </div>
      )}

      {backing && (
        <div className="card" style={{ borderColor: '#059669' }}>
          <h3>业务依据核验：{backing.title}</h3>
          <p>底层系统逻辑交叉实证环节数：<strong>{backing.logic_backing_count}</strong>（底层实现细节已隔离隐藏）</p>
          {(backing.clause_references || []).map((cl, i) => (
            <div key={i} style={{ fontSize: 13, marginBottom: 6 }}>
              <strong>[{cl.chapter}] {cl.clause_title}：</strong>{cl.requirement}
            </div>
          ))}
          <button className="btn" onClick={() => setBacking(null)}>关闭</button>
        </div>
      )}
    </div>
  )
}
