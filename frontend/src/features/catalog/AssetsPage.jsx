import React, { useEffect, useMemo, useState } from 'react'
import {
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
  const [search, setSearch] = useState('')
  const [capFilter, setCapFilter] = useState('')
  const [catFilter, setCatFilter] = useState('')
  const [recFilter, setRecFilter] = useState('')
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

  const handleSaveEdit = async () => {
    if (!editing) return
    setError(''); setNote('')
    try {
      if (editing.kind === 'RULE') {
        await editRule(editing.row.id, {
          expected_version: editing.row.current_version,
          editor: '业务专员',
          reason: '补充优化业务解释',
          changes: editing.changes,
        })
      } else {
        await editCaliber(editing.row.id, {
          expected_version: editing.row.current_version,
          editor: '业务专员',
          reason: '补充优化口径解释',
          changes: editing.changes,
        })
      }
      setEditing(null)
      setNote('业务解释已保存（系统门禁已确认不含任何技术黑话或表字段名称）')
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

  const capabilities = data?.capabilities || []
  const capabilityNames = useMemo(() => capabilities.map((c) => c.name), [capabilities])
  const qLower = search.trim().toLowerCase()

  const filteredRules = useMemo(() => {
    return (data?.rules || []).filter((r) => {
      if (capFilter && r.capability_name !== capFilter) return false
      if (catFilter && r.rule_category !== catFilter) return false
      if (recFilter && r.reconcile_status !== recFilter) return false
      if (qLower) {
        const hay = `${r.title} ${r.capability_name} ${r.business_condition} ${r.business_action} ${r.business_rationale}`.toLowerCase()
        if (!hay.includes(qLower)) return false
      }
      return true
    })
  }, [data, capFilter, catFilter, recFilter, qLower])

  const filteredCalibers = useMemo(() => {
    return (data?.calibers || []).filter((c) => {
      if (capFilter && c.capability_name !== capFilter) return false
      if (recFilter && c.reconcile_status !== recFilter) return false
      if (qLower) {
        const hay = `${c.metric_name} ${c.capability_name} ${c.business_definition} ${c.formula_summary} ${c.conflict_notes}`.toLowerCase()
        if (!hay.includes(qLower)) return false
      }
      return true
    })
  }, [data, capFilter, recFilter, qLower])

  const filteredReconcile = useMemo(() => {
    return (data?.reconcile?.items || []).filter((item) => {
      if (capFilter && item.capability_name !== capFilter) return false
      if (recFilter && item.reconcile_status !== recFilter) return false
      if (qLower) {
        const hay = `${item.name} ${item.capability_name} ${item.summary}`.toLowerCase()
        if (!hay.includes(qLower)) return false
      }
      return true
    })
  }, [data, capFilter, recFilter, qLower])

  const stats = data?.stats || {}

  return (
    <div>
      <div className="card">
        <h2>第二步：业务逻辑全景与业务解释中心</h2>
        <p style={{ color: '#475569', fontSize: 13, marginTop: 4 }}>
          直观了解“当前系统逻辑实际包含了哪些业务、底层条件是怎么跑的、以及每条逻辑对应的业务含义解释与制度对照”。
        </p>
        <div className="grid-4" style={{ marginTop: 12 }}>
          <div className="card" style={{ marginBottom: 0 }}><strong>{stats.capability_count || 0}</strong><div>已识别业务板块</div></div>
          <div className="card" style={{ marginBottom: 0 }}><strong>{stats.covered_capabilities || 0}</strong><div>系统逻辑已涵盖板块</div></div>
          <div className="card" style={{ marginBottom: 0 }}><strong>{stats.rule_count || 0}</strong><div>纳入 / 剔除 / 分支规则</div></div>
          <div className="card" style={{ marginBottom: 0 }}><strong>{stats.caliber_count || 0}</strong><div>业务指标口径卡</div></div>
        </div>
        {note && <p style={{ color: '#047857', marginTop: 10, fontWeight: 600 }}>{note}</p>}
        {error && <p style={{ color: '#b91c1c', marginTop: 10 }}>{error}</p>}
      </div>

      <div className="tabs">
        <button className={`tab-btn ${tab === 'capabilities' ? 'active' : ''}`} onClick={() => setTab('capabilities')}>
          1. 业务版图（{capabilities.length}）
        </button>
        <button className={`tab-btn ${tab === 'rules' ? 'active' : ''}`} onClick={() => setTab('rules')}>
          2. 逻辑与业务解释（{filteredRules.length}）
        </button>
        <button className={`tab-btn ${tab === 'calibers' ? 'active' : ''}`} onClick={() => setTab('calibers')}>
          3. 指标口径解释（{filteredCalibers.length}）
        </button>
        <button className={`tab-btn ${tab === 'reconcile' ? 'active' : ''}`} onClick={() => setTab('reconcile')}>
          4. 制度 vs 逻辑差异对照（{filteredReconcile.length}）
        </button>
      </div>

      {tab !== 'capabilities' && (
        <div className="card filter-bar">
          <input
            className="input"
            placeholder="搜索规则标题、业务关键词或口径解释…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ flex: '2 1 220px' }}
          />
          <select value={capFilter} onChange={(e) => setCapFilter(e.target.value)} style={{ flex: '1 1 200px' }}>
            <option value="">全部业务板块</option>
            {capabilityNames.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          {tab === 'rules' && (
            <select value={catFilter} onChange={(e) => setCatFilter(e.target.value)} style={{ flex: '1 1 140px' }}>
              <option value="">全部规则类型</option>
              {Object.entries(RULE_CATEGORY_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          )}
          <select value={recFilter} onChange={(e) => setRecFilter(e.target.value)} style={{ flex: '1 1 160px' }}>
            <option value="">全部对照状态</option>
            {Object.entries(RECONCILE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </select>
          {(search || capFilter || catFilter || recFilter) && (
            <button
              className="btn"
              onClick={() => { setSearch(''); setCapFilter(''); setCatFilter(''); setRecFilter('') }}
            >
              清空筛选
            </button>
          )}
        </div>
      )}

      {tab === 'capabilities' && (
        <div className="grid-2">
          {capabilities.map((c) => {
            const cov = formatBadge(COVERAGE_LABELS, c.coverage_status)
            return (
              <div key={c.id} className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <h3 style={{ margin: '0 0 6px' }}>{c.name}</h3>
                  <span className={`badge badge-${cov.tone}`}>{cov.label}</span>
                </div>
                <p style={{ fontSize: 14, color: '#334155', margin: '8px 0' }}>{c.description}</p>
                <p style={{ fontSize: 13, color: '#475569', margin: '6px 0' }}>
                  <strong>逻辑覆盖与加工要点：</strong>{c.scope_summary}
                </p>
                {((c.upstream_businesses || []).length > 0 || (c.downstream_businesses || []).length > 0) && (
                  <div className="lineage-row">
                    {(c.upstream_businesses || []).length > 0 && (
                      <span><strong>上游依赖业务：</strong>{c.upstream_businesses.join('、')}</span>
                    )}
                    {(c.downstream_businesses || []).length > 0 && (
                      <span><strong>流向下游业务：</strong>{c.downstream_businesses.join('、')}</span>
                    )}
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12, color: '#64748b' }}>
                    规则 {c.rule_count} 条 · 口径 {c.caliber_count} 项 · 底层逻辑实证 {c.logic_backing_count} 处 · 面向：{c.audience}
                  </span>
                  <button
                    className="btn"
                    onClick={() => { setCapFilter(c.name); setTab('rules') }}
                  >
                    查看该板块规则与业务解释
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {tab === 'rules' && filteredRules.map((r) => {
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
            <p style={{ fontSize: 14, marginTop: 10 }}><strong>当前逻辑实际执行：</strong>{r.business_condition}</p>
            <p style={{ fontSize: 14 }}><strong>对应业务处理结果：</strong>{r.business_action}</p>
            <p style={{ fontSize: 13, color: '#475569' }}><strong>业务含义解释：</strong>{r.business_rationale}</p>
            <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <button
                className="btn"
                onClick={() => setEditing({
                  kind: 'RULE',
                  row: r,
                  changes: {
                    business_condition: r.business_condition,
                    business_action: r.business_action,
                    business_rationale: r.business_rationale,
                  },
                })}
              >
                补充 / 优化业务解释
              </button>
              <button className="btn btn-primary" onClick={() => inspectBacking(r.code)}>查看制度对照与逻辑支撑 ({r.logic_backing_count})</button>
            </div>
          </div>
        )
      })}

      {tab === 'calibers' && filteredCalibers.map((c) => {
        const rec = formatBadge(RECONCILE_LABELS, c.reconcile_status)
        const st = formatBadge(STATUS_LABELS, c.status)
        return (
          <div key={c.id} className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <strong>{c.metric_name}</strong>
                <span style={{ marginLeft: 8, fontSize: 12, color: '#64748b' }}>（{c.capability_name}）</span>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <span className={`badge badge-${rec.tone}`}>{rec.label}</span>
                <span className={`badge badge-${st.tone}`}>{st.label}</span>
              </div>
            </div>
            <p style={{ fontSize: 14, marginTop: 8 }}><strong>业务定义：</strong>{c.business_definition}</p>
            <p style={{ fontSize: 14 }}><strong>系统实际计算口径：</strong>{c.formula_summary}</p>
            <p style={{ fontSize: 13, color: '#475569' }}><strong>易混口径提醒：</strong>{c.distinction_notes}</p>
            {c.conflict_notes && <div className="disclosure-box">{c.conflict_notes}</div>}
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button
                className="btn"
                onClick={() => setEditing({
                  kind: 'CALIBER',
                  row: c,
                  changes: { formula_summary: c.formula_summary, distinction_notes: c.distinction_notes },
                })}
              >
                补充 / 优化口径解释
              </button>
              <button className="btn btn-primary" onClick={() => inspectBacking(c.code)}>查看依据</button>
            </div>
          </div>
        )
      })}

      {tab === 'reconcile' && (
        <div className="card">
          {filteredReconcile.map((item) => {
            const rec = formatBadge(RECONCILE_LABELS, item.reconcile_status)
            return (
              <div key={item.code} style={{ padding: '10px 0', borderBottom: '1px solid #e2e8f0', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                <div>
                  <strong>{item.name}</strong> <span style={{ color: '#64748b', fontSize: 12 }}>({item.capability_name})</span>
                  <div style={{ fontSize: 13, color: '#475569', marginTop: 4 }}>{item.summary}</div>
                </div>
                <span className={`badge badge-${rec.tone}`} style={{ alignSelf: 'center', whiteSpace: 'nowrap' }}>{rec.label}</span>
              </div>
            )
          })}
        </div>
      )}

      {editing && (
        <div className="card modal-card">
          <h3>补充 / 优化业务解释（门禁自动校验，禁止输入代码或表名）</h3>
          {Object.entries(editing.changes).map(([k, v]) => (
            <div key={k} style={{ marginBottom: 10 }}>
              <textarea
                rows={3}
                value={v}
                onChange={(e) => setEditing({ ...editing, changes: { ...editing.changes, [k]: e.target.value } })}
              />
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary" onClick={handleSaveEdit}>保存业务解释</button>
            <button className="btn" onClick={() => setEditing(null)}>取消</button>
          </div>
        </div>
      )}

      {backing && (
        <div className="card modal-card" style={{ borderColor: '#059669' }}>
          <h3>制度对照与系统逻辑实证：{backing.title}</h3>
          <p>底层系统逻辑交叉实证环节数：<strong>{backing.logic_backing_count}</strong> 处（物理表名、字段与代码行号已隔离隐藏）</p>
          {(backing.clause_references || []).length === 0 ? (
            <p style={{ fontSize: 13, color: '#64748b' }}>暂无对应制度原文条款，该规则由底层系统处理逻辑直接还原生成。</p>
          ) : (
            (backing.clause_references || []).map((cl, i) => (
              <div key={i} style={{ fontSize: 13, marginBottom: 8, padding: 8, background: '#f8fafc', borderRadius: 6 }}>
                <strong>[{cl.chapter}] {cl.clause_title}：</strong>{cl.requirement}
              </div>
            ))
          )}
          <button className="btn" onClick={() => setBacking(null)}>关闭</button>
        </div>
      )}
    </div>
  )
}
