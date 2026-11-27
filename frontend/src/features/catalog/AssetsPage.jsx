import React, { useEffect, useMemo, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import {
  editCaliber,
  editRule,
  getAssetBacking,
  getPanorama,
} from '../../api/catalog.js'
import { BANK_AGENCIES, agencyForRegime, agencyMeta, matchesAgencyOrRegime } from '../../utils/bankingTaxonomy.js'
import { AlertIcon, CheckCircleIcon, CloseIcon, FilterIcon, SearchIcon } from '../../utils/icons.jsx'
import {
  COVERAGE_LABELS,
  EDIT_FIELD_LABELS,
  FINANCIAL_SUBJECT_THEMES,
  FINANCIAL_SUBJECT_THEME_DESCRIPTIONS,
  RECONCILE_LABELS,
  RULE_CATEGORY_LABELS,
  STATUS_LABELS,
  formatBadge,
} from '../../utils/labels.js'

const PAGE_SIZE = 20

const DOT_TONE_CLASS = {
  green: 'dot-green',
  amber: 'dot-amber',
  blue: 'dot-blue',
  slate: 'dot-slate',
}

export default function AssetsPage() {
  const navigate = useNavigate()
  const outletContext = useOutletContext() || {}
  const { refreshStats } = outletContext

  const [data, setData] = useState(null)
  const [tab, setTab] = useState('capabilities')
  const [capViewMode, setCapViewMode] = useState('vertical')
  const [search, setSearch] = useState('')
  const [agencyFilter, setAgencyFilter] = useState('ALL')
  const [regimeFilter, setRegimeFilter] = useState('')
  const [capFilter, setCapFilter] = useState('')
  const [subDomainFilter, setSubDomainFilter] = useState('')
  const [themeFilter, setThemeFilter] = useState('')
  const [catFilter, setCatFilter] = useState('')
  const [recFilter, setRecFilter] = useState('')
  const [showMoreFilters, setShowMoreFilters] = useState(false)
  const [page, setPage] = useState(1)

  const [editing, setEditing] = useState(null)
  const [drawerItem, setDrawerItem] = useState(null)
  const [backing, setBacking] = useState(null)
  const [backingLoading, setBackingLoading] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const reload = async () => {
    try {
      setData(await getPanorama())
      if (refreshStats) refreshStats()
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => {
    reload()
  }, [])

  useEffect(() => {
    setPage(1)
  }, [tab, search, agencyFilter, regimeFilter, capFilter, subDomainFilter, themeFilter, catFilter, recFilter])

  useEffect(() => {
    if (!editing && !drawerItem) return undefined
    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (editing) setEditing(null)
        else if (drawerItem) {
          setDrawerItem(null)
          setBacking(null)
        }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [editing, drawerItem])

  const openDetailDrawer = async (kind, row) => {
    setError('')
    setDrawerItem({ kind, row })
    setBacking(null)
    setBackingLoading(true)
    try {
      setBacking(await getAssetBacking(row.code))
    } catch (e) {
      setError(e.message)
    } finally {
      setBackingLoading(false)
    }
  }

  const closeDetailDrawer = () => {
    setDrawerItem(null)
    setBacking(null)
  }

  const openEditForRule = (r) => {
    setEditing({
      kind: 'RULE',
      row: r,
      changes: {
        business_condition: r.business_condition,
        business_action: r.business_action,
        business_rationale: r.business_rationale,
      },
    })
  }

  const openEditForCaliber = (c) => {
    setEditing({
      kind: 'CALIBER',
      row: c,
      changes: {
        formula_summary: c.formula_summary,
        distinction_notes: c.distinction_notes,
      },
    })
  }

  const handleSaveEdit = async () => {
    if (!editing) return
    setError('')
    setNote('')
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
      setNote('业务释义已更新并同步至知识底座')
      await reload()
    } catch (e) {
      setError(e.message)
    }
  }

  const capabilities = data?.capabilities || []
  const capabilityNames = useMemo(() => capabilities.map((c) => c.name), [capabilities])
  const regimeOptions = useMemo(() => {
    const regs = (data?.taxonomy?.regimes || []).map((x) => x.regime)
    if (regs.length > 0) return regs
    return Array.from(new Set(capabilities.map((c) => c.regime).filter(Boolean)))
  }, [data, capabilities])

  const subDomainOptions = useMemo(() => {
    if (capFilter) {
      const capObj = capabilities.find((c) => c.name === capFilter)
      if (capObj?.sub_domains?.length) {
        return capObj.sub_domains.map((s) => s.name)
      }
    }
    const items = [...(data?.rules || []), ...(data?.calibers || [])]
    return Array.from(new Set(items.map((x) => x.sub_domain).filter(Boolean)))
  }, [data, capabilities, capFilter])

  const themeMatrix = useMemo(() => {
    if (data?.taxonomy?.subject_themes?.length) return data.taxonomy.subject_themes
    return FINANCIAL_SUBJECT_THEMES.map((theme) => ({
      theme,
      rule_count: 0,
      caliber_count: 0,
      total_assets: 0,
      capability_count: 0,
      capabilities: [],
    }))
  }, [data])

  const visibleCapabilities = useMemo(() => {
    return capabilities.filter((c) => matchesAgencyOrRegime(c.regime, { agency: agencyFilter, regime: regimeFilter }))
  }, [capabilities, agencyFilter, regimeFilter])

  const qLower = search.trim().toLowerCase()

  const filteredRules = useMemo(() => {
    return (data?.rules || []).filter((r) => {
      if (!matchesAgencyOrRegime(r.regime, { agency: agencyFilter, regime: regimeFilter })) return false
      if (capFilter && r.capability_name !== capFilter) return false
      if (subDomainFilter && r.sub_domain !== subDomainFilter) return false
      if (themeFilter && r.subject_theme !== themeFilter) return false
      if (catFilter && r.rule_category !== catFilter) return false
      if (recFilter && r.reconcile_status !== recFilter) return false
      if (qLower) {
        const hay = `${r.title} ${r.capability_name} ${r.regime || ''} ${r.sub_domain || ''} ${r.subject_theme || ''} ${r.business_condition} ${r.business_action} ${r.business_rationale}`.toLowerCase()
        if (!hay.includes(qLower)) return false
      }
      return true
    })
  }, [data, agencyFilter, regimeFilter, capFilter, subDomainFilter, themeFilter, catFilter, recFilter, qLower])

  const filteredCalibers = useMemo(() => {
    return (data?.calibers || []).filter((c) => {
      if (!matchesAgencyOrRegime(c.regime, { agency: agencyFilter, regime: regimeFilter })) return false
      if (capFilter && c.capability_name !== capFilter) return false
      if (subDomainFilter && c.sub_domain !== subDomainFilter) return false
      if (themeFilter && c.subject_theme !== themeFilter) return false
      if (recFilter && c.reconcile_status !== recFilter) return false
      if (qLower) {
        const hay = `${c.metric_name} ${c.capability_name} ${c.regime || ''} ${c.sub_domain || ''} ${c.subject_theme || ''} ${c.business_definition} ${c.formula_summary} ${c.conflict_notes}`.toLowerCase()
        if (!hay.includes(qLower)) return false
      }
      return true
    })
  }, [data, agencyFilter, regimeFilter, capFilter, subDomainFilter, themeFilter, recFilter, qLower])

  const reconcileAll = data?.reconcile?.items || []
  const reconcileSummary = data?.reconcile?.summary || {}

  const filteredReconcile = useMemo(() => {
    return reconcileAll.filter((item) => {
      if (!matchesAgencyOrRegime(item.regime, { agency: agencyFilter, regime: regimeFilter })) return false
      if (capFilter && item.capability_name !== capFilter) return false
      if (subDomainFilter && item.sub_domain !== subDomainFilter) return false
      if (themeFilter && item.subject_theme !== themeFilter) return false
      if (recFilter && item.reconcile_status !== recFilter) return false
      if (qLower) {
        const hay = `${item.name} ${item.capability_name} ${item.regime || ''} ${item.sub_domain || ''} ${item.subject_theme || ''} ${item.summary}`.toLowerCase()
        if (!hay.includes(qLower)) return false
      }
      return true
    })
  }, [reconcileAll, agencyFilter, regimeFilter, capFilter, subDomainFilter, themeFilter, recFilter, qLower])

  const stats = data?.stats || {}
  const hasAnyFilter = Boolean(search || agencyFilter !== 'ALL' || regimeFilter || capFilter || subDomainFilter || themeFilter || catFilter || recFilter)
  const clearAllFilters = () => {
    setSearch('')
    setAgencyFilter('ALL')
    setRegimeFilter('')
    setCapFilter('')
    setSubDomainFilter('')
    setThemeFilter('')
    setCatFilter('')
    setRecFilter('')
  }

  const agencyCounts = useMemo(() => {
    const counts = { ALL: capabilities.length }
    for (const c of capabilities) {
      const ag = agencyForRegime(c.regime)
      counts[ag] = (counts[ag] || 0) + 1
    }
    return counts
  }, [capabilities])

  const regimesInActiveAgency = useMemo(() => {
    if (agencyFilter === 'ALL') return regimeOptions
    const allowed = BANK_AGENCIES.find((a) => a.code === agencyFilter)?.regimes || []
    const present = regimeOptions.filter((r) => allowed.includes(r))
    return present.length > 0 ? present : allowed
  }, [agencyFilter, regimeOptions])

  const activeRows = tab === 'rules' ? filteredRules : tab === 'calibers' ? filteredCalibers : filteredReconcile
  const totalPages = Math.max(1, Math.ceil(activeRows.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const pagedRows = activeRows.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">业务资产</h1>
          <p className="page-subtitle">
            按商业银行监管机构、监管报表、一级业务域与金融十大主题统一查阅、核对与补充业务规则与指标口径。
          </p>
        </div>
        <div className="page-header-actions">
          <button type="button" className="btn btn-sm" onClick={() => navigate('/admin/versions')}>
            变更与对账整改 →
          </button>
        </div>
      </div>

      {/* 商业银行两级监管机构 -> 监管报表筛选条 */}
      <section className="dbc-taxonomy-bar" style={{ marginBottom: 14 }} aria-label="按商业银行监管机构与报表筛选">
        <div className="dbc-tax-row">
          <span className="dbc-tax-label">监管机构</span>
          <div className="dbc-tax-pills">
            <button
              type="button"
              className={`dbc-agency-pill ${agencyFilter === 'ALL' ? 'active' : ''}`}
              onClick={() => { setAgencyFilter('ALL'); setRegimeFilter('') }}
            >
              全部机构 <span>{agencyCounts.ALL || 0}</span>
            </button>
            {BANK_AGENCIES.map((ag) => (
              <button
                key={ag.code}
                type="button"
                className={`dbc-agency-pill ${agencyFilter === ag.code ? 'active' : ''}`}
                title={ag.hint}
                onClick={() => { setAgencyFilter(ag.code); setRegimeFilter('') }}
              >
                {ag.label} <span>{agencyCounts[ag.code] || 0}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="dbc-tax-row">
          <span className="dbc-tax-label">监管报表</span>
          <div className="dbc-tax-pills">
            <button
              type="button"
              className={`dbc-regime-pill ${regimeFilter === '' ? 'active' : ''}`}
              onClick={() => setRegimeFilter('')}
            >
              {agencyFilter === 'ALL' ? '全部体系' : `${agencyMeta(agencyFilter).shortLabel}全部体系`}
            </button>
            {regimesInActiveAgency.map((reg) => (
              <button
                key={reg}
                type="button"
                className={`dbc-regime-pill ${regimeFilter === reg ? 'active' : ''}`}
                onClick={() => setRegimeFilter(reg)}
              >
                {reg}
              </button>
            ))}
          </div>
        </div>
      </section>

      <div className="stat-strip">
        <div className="stat-cell">
          <div className="stat-cell-label">一级业务域</div>
          <div className="stat-cell-value">{stats.capability_count || 0}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-cell-label">已覆盖板块</div>
          <div className="stat-cell-value">{stats.covered_capabilities || 0}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-cell-label">处理规则</div>
          <div className="stat-cell-value">{stats.rule_count || 0}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-cell-label">指标口径</div>
          <div className="stat-cell-value">{stats.caliber_count || 0}</div>
        </div>
      </div>

      {note && <div className="alert-banner success" style={{ marginBottom: 14 }}>{note}</div>}
      {error && <div className="alert-banner error" style={{ marginBottom: 14 }}>{error}</div>}

      <div className="underline-tabs">
        <button type="button" className={`underline-tab ${tab === 'capabilities' ? 'active' : ''}`} onClick={() => setTab('capabilities')}>
          业务版图（{capabilities.length}）
        </button>
        <button type="button" className={`underline-tab ${tab === 'rules' ? 'active' : ''}`} onClick={() => setTab('rules')}>
          处理规则（{filteredRules.length}）
        </button>
        <button type="button" className={`underline-tab ${tab === 'calibers' ? 'active' : ''}`} onClick={() => setTab('calibers')}>
          指标口径（{filteredCalibers.length}）
        </button>
        <button type="button" className={`underline-tab ${tab === 'reconcile' ? 'active' : ''}`} onClick={() => setTab('reconcile')}>
          制度对照（{filteredReconcile.length}）
        </button>
      </div>

      {tab === 'capabilities' && (
        <div>
          <div className="toolbar-row" style={{ justifyContent: 'space-between' }}>
            <div className="segment-control" style={{ display: 'inline-flex' }}>
              <button
                type="button"
                className={`segment-btn ${capViewMode === 'vertical' ? 'active' : ''}`}
                onClick={() => setCapViewMode('vertical')}
              >
                按业务域
              </button>
              <button
                type="button"
                className={`segment-btn ${capViewMode === 'theme' ? 'active' : ''}`}
                onClick={() => setCapViewMode('theme')}
              >
                按金融十大主题
              </button>
            </div>

            {capViewMode === 'vertical' && (
              <div className="pill-row">
                <button
                  type="button"
                  className={`pill-chip ${regimeFilter === '' ? 'active' : ''}`}
                  onClick={() => setRegimeFilter('')}
                >
                  全部体系
                </button>
                {regimeOptions.map((reg) => (
                  <button
                    key={reg}
                    type="button"
                    className={`pill-chip ${regimeFilter === reg ? 'active' : ''}`}
                    onClick={() => setRegimeFilter(reg)}
                  >
                    {reg}
                  </button>
                ))}
              </div>
            )}
          </div>

          {capViewMode === 'vertical' ? (
            <div className="domain-grid">
              {visibleCapabilities.map((c) => {
                const cov = formatBadge(COVERAGE_LABELS, c.coverage_status)
                const subDomains = c.sub_domains || []
                const shownSubs = subDomains.slice(0, 4)
                const extraSubCount = Math.max(0, subDomains.length - 4)
                return (
                  <div key={c.id} className="domain-card">
                    <div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                        <span className="tag tag-blue">{c.regime || '通用监管'}</span>
                        <span className="status-inline">
                          <span className={`dot ${DOT_TONE_CLASS[cov.tone] || 'dot-slate'}`} />
                          <span>{cov.label}</span>
                        </span>
                      </div>
                      <h3 style={{ margin: '0 0 6px', fontSize: 15.5 }}>{c.name}</h3>
                      <p className="domain-desc">{c.description}</p>
                    </div>

                    <div>
                      {shownSubs.length > 0 && (
                        <div className="pill-row" style={{ marginBottom: 10 }}>
                          {shownSubs.map((sd) => (
                            <button
                              key={sd.name}
                              type="button"
                              className="pill-chip"
                              onClick={() => {
                                setRegimeFilter('')
                                setCapFilter(c.name)
                                setSubDomainFilter(sd.name)
                                setThemeFilter('')
                                setTab('rules')
                              }}
                            >
                              {sd.name} ({sd.rule_count + sd.caliber_count})
                            </button>
                          ))}
                          {extraSubCount > 0 && (
                            <span className="tag">+{extraSubCount}</span>
                          )}
                        </div>
                      )}

                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 10, borderTop: '1px solid #f1f5f9' }}>
                        <span style={{ fontSize: 12, color: '#64748b' }}>
                          规则 {c.rule_count} · 口径 {c.caliber_count} · 实证 {c.logic_backing_count}
                        </span>
                        <button
                          type="button"
                          className="link-btn"
                          onClick={() => {
                            setCapFilter(c.name)
                            setSubDomainFilter('')
                            setThemeFilter('')
                            setTab('rules')
                          }}
                        >
                          查看规则 →
                        </button>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="domain-grid">
              {themeMatrix.map((item) => (
                <div key={item.theme} className="domain-card">
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <h3 style={{ margin: 0, fontSize: 15.5 }}>{item.theme}</h3>
                      <span className="tag">跨 {item.capability_count} 域</span>
                    </div>
                    <p className="domain-desc">
                      {FINANCIAL_SUBJECT_THEME_DESCRIPTIONS[item.theme] || ''}
                    </p>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 10, borderTop: '1px solid #f1f5f9' }}>
                    <span style={{ fontSize: 12, color: '#64748b' }}>
                      规则 {item.rule_count} · 口径 {item.caliber_count}
                    </span>
                    <button
                      type="button"
                      className="link-btn"
                      disabled={item.rule_count + item.caliber_count === 0}
                      onClick={() => {
                        setRegimeFilter('')
                        setCapFilter('')
                        setSubDomainFilter('')
                        setThemeFilter(item.theme)
                        setTab('rules')
                      }}
                    >
                      查看规则 →
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {(tab === 'rules' || tab === 'calibers') && (
        <>
          <div className="toolbar-row">
            <div style={{ position: 'relative', flex: '2 1 220px' }}>
              <SearchIcon
                size={14}
                style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#94a3b8' }}
              />
              <input
                className="input"
                style={{ paddingLeft: 30 }}
                placeholder="搜索名称、业务域或口径说明…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <select
              value={capFilter}
              onChange={(e) => {
                setCapFilter(e.target.value)
                setSubDomainFilter('')
              }}
              style={{ flex: '1 1 170px', maxWidth: 230 }}
            >
              <option value="">全部一级业务域</option>
              {capabilityNames.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
            {tab === 'rules' && (
              <select value={catFilter} onChange={(e) => setCatFilter(e.target.value)} style={{ flex: '1 1 130px', maxWidth: 160 }}>
                <option value="">全部类型</option>
                {Object.entries(RULE_CATEGORY_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            )}
            <select value={recFilter} onChange={(e) => setRecFilter(e.target.value)} style={{ flex: '1 1 140px', maxWidth: 180 }}>
              <option value="">全部状态</option>
              {Object.entries(RECONCILE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v.label}</option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setShowMoreFilters((v) => !v)}
            >
              <FilterIcon size={13} />
              <span>{showMoreFilters ? '收起筛选' : '更多筛选'}</span>
            </button>
            {hasAnyFilter && (
              <button type="button" className="link-btn" onClick={clearAllFilters}>
                重置
              </button>
            )}
          </div>

          {showMoreFilters && (
            <div className="toolbar-secondary">
              <select value={regimeFilter} onChange={(e) => setRegimeFilter(e.target.value)} style={{ flex: '1 1 150px' }}>
                <option value="">全部监管体系</option>
                {regimeOptions.map((reg) => (
                  <option key={reg} value={reg}>{reg}</option>
                ))}
              </select>
              <select value={subDomainFilter} onChange={(e) => setSubDomainFilter(e.target.value)} style={{ flex: '1 1 170px' }}>
                <option value="">全部二级子专题</option>
                {subDomainOptions.map((sd) => (
                  <option key={sd} value={sd}>{sd}</option>
                ))}
              </select>
              <select value={themeFilter} onChange={(e) => setThemeFilter(e.target.value)} style={{ flex: '1 1 170px' }}>
                <option value="">全部金融十大主题</option>
                {FINANCIAL_SUBJECT_THEMES.map((th) => (
                  <option key={th} value={th}>{th}</option>
                ))}
              </select>
            </div>
          )}
        </>
      )}

      {tab === 'rules' && (
        <div className="table-card">
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>规则名称与所属业务</th>
                  <th style={{ width: 100 }}>类型</th>
                  <th style={{ width: 150 }}>对照状态</th>
                  <th style={{ width: 110 }} className="num-col">操作</th>
                </tr>
              </thead>
              <tbody>
                {pagedRows.length === 0 ? (
                  <tr>
                    <td colSpan={4} style={{ textAlign: 'center', padding: '28px 14px', color: '#64748b' }}>
                      未找到符合筛选条件的处理规则
                    </td>
                  </tr>
                ) : (
                  pagedRows.map((r) => {
                    const rec = formatBadge(RECONCILE_LABELS, r.reconcile_status)
                    return (
                      <tr key={r.id} className="clickable" onClick={() => openDetailDrawer('RULE', r)}>
                        <td>
                          <div className="table-title-main">{r.title}</div>
                          <div className="table-title-sub">
                            {[r.capability_name, r.sub_domain, r.subject_theme].filter(Boolean).join(' · ')}
                          </div>
                        </td>
                        <td>
                          <span className="tag">{RULE_CATEGORY_LABELS[r.rule_category] || r.rule_category}</span>
                        </td>
                        <td>
                          <span className="status-inline">
                            <span className={`dot ${DOT_TONE_CLASS[rec.tone] || 'dot-slate'}`} />
                            <span>{rec.label}</span>
                          </span>
                        </td>
                        <td className="num-col" onClick={(e) => e.stopPropagation()}>
                          <button type="button" className="link-btn" onClick={() => openDetailDrawer('RULE', r)}>
                            详情
                          </button>
                          <span style={{ margin: '0 8px', color: '#cbd5e1' }}>|</span>
                          <button type="button" className="link-btn" onClick={() => openEditForRule(r)}>
                            编辑
                          </button>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
          <div className="pagination-bar">
            <span>共 {filteredRules.length} 条规则 · 第 {safePage} / {totalPages} 页</span>
            <div style={{ display: 'flex', gap: 6 }}>
              <button type="button" className="btn btn-sm" disabled={safePage <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                上一页
              </button>
              <button type="button" className="btn btn-sm" disabled={safePage >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                下一页
              </button>
            </div>
          </div>
        </div>
      )}

      {tab === 'calibers' && (
        <div className="table-card">
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>指标名称与业务定义</th>
                  <th style={{ width: 140 }}>金融主题</th>
                  <th style={{ width: 150 }}>对照状态</th>
                  <th style={{ width: 110 }} className="num-col">操作</th>
                </tr>
              </thead>
              <tbody>
                {pagedRows.length === 0 ? (
                  <tr>
                    <td colSpan={4} style={{ textAlign: 'center', padding: '28px 14px', color: '#64748b' }}>
                      未找到符合筛选条件的指标口径
                    </td>
                  </tr>
                ) : (
                  pagedRows.map((c) => {
                    const rec = formatBadge(RECONCILE_LABELS, c.reconcile_status)
                    return (
                      <tr key={c.id} className="clickable" onClick={() => openDetailDrawer('CALIBER', c)}>
                        <td>
                          <div className="table-title-main">{c.metric_name}</div>
                          <div className="table-title-sub">
                            {c.capability_name}{c.business_definition ? ` · ${c.business_definition}` : ''}
                          </div>
                        </td>
                        <td>
                          <span className="tag">{c.subject_theme || '未归类'}</span>
                        </td>
                        <td>
                          <span className="status-inline">
                            <span className={`dot ${DOT_TONE_CLASS[rec.tone] || 'dot-slate'}`} />
                            <span>{rec.label}</span>
                          </span>
                        </td>
                        <td className="num-col" onClick={(e) => e.stopPropagation()}>
                          <button type="button" className="link-btn" onClick={() => openDetailDrawer('CALIBER', c)}>
                            详情
                          </button>
                          <span style={{ margin: '0 8px', color: '#cbd5e1' }}>|</span>
                          <button type="button" className="link-btn" onClick={() => openEditForCaliber(c)}>
                            编辑
                          </button>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
          <div className="pagination-bar">
            <span>共 {filteredCalibers.length} 项口径 · 第 {safePage} / {totalPages} 页</span>
            <div style={{ display: 'flex', gap: 6 }}>
              <button type="button" className="btn btn-sm" disabled={safePage <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                上一页
              </button>
              <button type="button" className="btn btn-sm" disabled={safePage >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                下一页
              </button>
            </div>
          </div>
        </div>
      )}

      {tab === 'reconcile' && (
        <>
          <div className="toolbar-row" style={{ justifyContent: 'space-between' }}>
            <div className="pill-row">
              <button
                type="button"
                className={`pill-chip ${recFilter === '' ? 'active' : ''}`}
                onClick={() => setRecFilter('')}
              >
                全部 ({reconcileAll.length})
              </button>
              <button
                type="button"
                className={`pill-chip ${recFilter === 'DIVERGENT' ? 'active' : ''}`}
                onClick={() => setRecFilter('DIVERGENT')}
              >
                口径差异 ({reconcileSummary.DIVERGENT || 0})
              </button>
              <button
                type="button"
                className={`pill-chip ${recFilter === 'UNREALIZED' ? 'active' : ''}`}
                onClick={() => setRecFilter('UNREALIZED')}
              >
                尚未包含 ({reconcileSummary.UNREALIZED || 0})
              </button>
              <button
                type="button"
                className={`pill-chip ${recFilter === 'VERIFIED' ? 'active' : ''}`}
                onClick={() => setRecFilter('VERIFIED')}
              >
                已验证一致 ({(reconcileSummary.VERIFIED || 0) + (reconcileSummary.CONSISTENT || 0)})
              </button>
            </div>

            <div style={{ position: 'relative', flex: '1 1 220px', maxWidth: 320 }}>
              <SearchIcon
                size={14}
                style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#94a3b8' }}
              />
              <input
                className="input"
                style={{ paddingLeft: 30 }}
                placeholder="搜索对账事项名称或摘要…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          <div className="table-card">
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>对账事项与所属业务</th>
                    <th>对照摘要</th>
                    <th style={{ width: 150 }}>对照状态</th>
                    <th style={{ width: 90 }} className="num-col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {pagedRows.length === 0 ? (
                    <tr>
                      <td colSpan={4} style={{ textAlign: 'center', padding: '28px 14px', color: '#64748b' }}>
                        未找到符合条件的制度对照记录
                      </td>
                    </tr>
                  ) : (
                    pagedRows.map((item) => {
                      const rec = formatBadge(RECONCILE_LABELS, item.reconcile_status)
                      return (
                        <tr key={item.code} className="clickable" onClick={() => openDetailDrawer('RECONCILE', item)}>
                          <td>
                            <div className="table-title-main">{item.name}</div>
                            <div className="table-title-sub">
                              {[item.capability_name, item.sub_domain].filter(Boolean).join(' · ')}
                            </div>
                          </td>
                          <td>
                            <div className="table-title-sub" style={{ WebkitLineClamp: 2 }}>{item.summary}</div>
                          </td>
                          <td>
                            <span className="status-inline">
                              <span className={`dot ${DOT_TONE_CLASS[rec.tone] || 'dot-slate'}`} />
                              <span>{rec.label}</span>
                            </span>
                          </td>
                          <td className="num-col">
                            <button
                              type="button"
                              className="link-btn"
                              onClick={(e) => {
                                e.stopPropagation()
                                openDetailDrawer('RECONCILE', item)
                              }}
                            >
                              查看依据
                            </button>
                          </td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
            <div className="pagination-bar">
              <span>共 {filteredReconcile.length} 项对照 · 第 {safePage} / {totalPages} 页</span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button type="button" className="btn btn-sm" disabled={safePage <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                  上一页
                </button>
                <button type="button" className="btn btn-sm" disabled={safePage >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                  下一页
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {editing && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="编辑业务释义"
          onClick={(e) => {
            if (e.target === e.currentTarget) setEditing(null)
          }}
        >
          <div className="modal-dialog">
            <div className="modal-header">
              <div>
                <h3 style={{ margin: 0, fontSize: 16 }}>补充 / 优化业务释义</h3>
                <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                  请使用纯业务语言描述，保存前系统将自动校验口径规范
                </div>
              </div>
              <button type="button" className="icon-btn" onClick={() => setEditing(null)} aria-label="关闭弹窗">
                <CloseIcon size={18} />
              </button>
            </div>
            <div className="modal-body">
              {Object.entries(editing.changes).map(([k, v]) => (
                <div key={k} style={{ marginBottom: 14 }}>
                  <label className="form-label">{EDIT_FIELD_LABELS[k] || k}</label>
                  <textarea
                    rows={3}
                    value={v}
                    onChange={(e) => setEditing({ ...editing, changes: { ...editing.changes, [k]: e.target.value } })}
                  />
                </div>
              ))}
            </div>
            <div className="modal-footer">
              <button type="button" className="btn" onClick={() => setEditing(null)}>取消</button>
              <button type="button" className="btn btn-primary" onClick={handleSaveEdit}>保存释义</button>
            </div>
          </div>
        </div>
      )}

      {drawerItem && (
        <div
          className="drawer-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="业务资产详情"
          onClick={(e) => {
            if (e.target === e.currentTarget) closeDetailDrawer()
          }}
        >
          <aside className="drawer-panel">
            <div className="drawer-header">
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                  <span className="tag tag-blue">
                    {drawerItem.kind === 'RULE' ? '处理规则' : drawerItem.kind === 'CALIBER' ? '指标口径' : '制度对照'}
                  </span>
                  {drawerItem.row.reconcile_status && (() => {
                    const rec = formatBadge(RECONCILE_LABELS, drawerItem.row.reconcile_status)
                    return (
                      <span className="status-inline">
                        <span className={`dot ${DOT_TONE_CLASS[rec.tone] || 'dot-slate'}`} />
                        <span>{rec.label}</span>
                      </span>
                    )
                  })()}
                  {drawerItem.row.status && (
                    <span className="tag">{formatBadge(STATUS_LABELS, drawerItem.row.status).label}</span>
                  )}
                </div>
                <h3 style={{ margin: 0, fontSize: 16, lineHeight: 1.4 }}>
                  {drawerItem.row.title || drawerItem.row.metric_name || drawerItem.row.name}
                </h3>
                <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>
                  {[drawerItem.row.capability_name, drawerItem.row.sub_domain, drawerItem.row.subject_theme]
                    .filter(Boolean)
                    .join(' · ')}
                </div>
              </div>
              <button type="button" className="icon-btn" onClick={closeDetailDrawer} aria-label="关闭抽屉">
                <CloseIcon size={18} />
              </button>
            </div>

            <div className="drawer-body">
              {drawerItem.kind === 'RULE' && (
                <>
                  <div className="drawer-detail-block">
                    <div className="drawer-detail-label">当前逻辑实际执行</div>
                    <div className="drawer-detail-text">{drawerItem.row.business_condition}</div>
                  </div>
                  <div className="drawer-detail-block">
                    <div className="drawer-detail-label">对应业务处理结果</div>
                    <div className="drawer-detail-text">{drawerItem.row.business_action}</div>
                  </div>
                  <div className="drawer-detail-block">
                    <div className="drawer-detail-label">业务含义解释</div>
                    <div className="drawer-detail-text">{drawerItem.row.business_rationale}</div>
                  </div>
                </>
              )}

              {drawerItem.kind === 'CALIBER' && (
                <>
                  <div className="drawer-detail-block">
                    <div className="drawer-detail-label">业务定义</div>
                    <div className="drawer-detail-text">{drawerItem.row.business_definition}</div>
                  </div>
                  <div className="drawer-detail-block">
                    <div className="drawer-detail-label">系统实际计算口径</div>
                    <div className="drawer-detail-text">{drawerItem.row.formula_summary}</div>
                  </div>
                  {drawerItem.row.distinction_notes && (
                    <div className="drawer-detail-block">
                      <div className="drawer-detail-label">易混口径提醒</div>
                      <div className="drawer-detail-text">{drawerItem.row.distinction_notes}</div>
                    </div>
                  )}
                </>
              )}

              {drawerItem.kind === 'RECONCILE' && (
                <div className="drawer-detail-block">
                  <div className="drawer-detail-label">对照摘要</div>
                  <div className="drawer-detail-text">{drawerItem.row.summary}</div>
                </div>
              )}

              {backingLoading && (
                <div className="sub-card" style={{ fontSize: 13, color: '#64748b' }}>
                  正在调取制度对照与系统实证…
                </div>
              )}

              {backing && (
                <>
                  <div className="evidence-stat-box">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7, color: '#0f766e', fontWeight: 700, fontSize: 13.5 }}>
                      <CheckCircleIcon size={16} />
                      <span>系统运行实证：已关联 {backing.logic_backing_count ?? 0} 处核验记录</span>
                    </div>
                    <p style={{ margin: '6px 0 0', fontSize: 12.5, color: '#115e59', lineHeight: 1.55 }}>
                      该业务口径已结合制度文档与系统实际运行规则完成交叉比对。
                    </p>
                  </div>

                  {backing.conflict_notes && (
                    <div className="disclosure-box">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, marginBottom: 4 }}>
                        <AlertIcon size={15} />
                        <span>口径差异说明</span>
                      </div>
                      <div>{backing.conflict_notes}</div>
                    </div>
                  )}

                  {(backing.processing_steps || []).length > 0 && (
                    <div style={{ marginTop: 14 }}>
                      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
                        系统实际加工步骤（{backing.processing_steps.length} 个核心环节）
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {backing.processing_steps.map((st, idx) => (
                          <React.Fragment key={idx}>
                          {st.flow_label && (idx === 0 || backing.processing_steps[idx - 1].flow_label !== st.flow_label) && (
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

                  <div style={{ marginTop: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
                      对应制度原文条款（{(backing.clause_references || []).length} 条）
                    </div>
                    {(backing.clause_references || []).length === 0 ? (
                      <div className="sub-card" style={{ fontSize: 13, color: '#64748b' }}>
                        暂无对应制度原文条款，该规则由系统实际处理逻辑梳理生成。
                      </div>
                    ) : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {backing.clause_references.map((cl, i) => (
                          <div key={i} className="sub-card">
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
              <div>
                {drawerItem.kind === 'RULE' && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => openEditForRule(drawerItem.row)}
                  >
                    编辑业务释义
                  </button>
                )}
                {drawerItem.kind === 'CALIBER' && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => openEditForCaliber(drawerItem.row)}
                  >
                    编辑口径释义
                  </button>
                )}
              </div>
              <button type="button" className="btn btn-sm btn-primary" onClick={closeDetailDrawer}>
                关闭
              </button>
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
