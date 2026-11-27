import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  confirmCaliber,
  confirmRule,
  exportRuleStudioHandoff,
  listAssetRevisions,
  listReconcileLedger,
} from '../../api/catalog.js'
import SelectDropdown from '../../components/SelectDropdown.jsx'
import { BANK_AGENCIES, agencyForRegime, agencyMeta } from '../../utils/bankingTaxonomy.js'
import { formatTime } from '../../utils/dateTime.js'
import { EDIT_FIELD_LABELS, RECONCILE_LABELS, formatBadge } from '../../utils/labels.js'

const ASSET_KIND_OPTIONS = [
  { value: 'ALL', label: '全部业务资产' },
  { value: 'RULE', label: '处理规则卡' },
  { value: 'CALIBER', label: '指标口径卡' },
]

const REVISION_KIND_OPTIONS = [
  { value: 'ALL', label: '全部变更类型' },
  { value: 'WORDING_EDIT', label: '人工优化业务释义' },
  { value: 'CONFIRM', label: '人工确权确认' },
]

const GAP_STATUS_OPTIONS = [
  { value: 'GAPS', label: '仅看分歧与未落地（推荐）' },
  { value: 'DIVERGENT', label: '仅看多套口径分歧' },
  { value: 'UNREALIZED', label: '仅看当前系统尚未包含' },
  { value: 'ALL', label: '全部制度对账台账' },
]

const REVISION_LABELS = {
  WORDING_EDIT: { label: '人工优化释义', tone: 'amber' },
  CONFIRM: { label: '人工业务确认', tone: 'green' },
  HUMAN_EDIT: { label: '人工修订', tone: 'amber' },
}

function buildReconcileReviewMarkdown(items, agencyLabel, regimeLabel) {
  const lines = [
    '# 监管制度对账与业务口径整改评审报告',
    '',
    `- **监管范围**：${agencyLabel} / ${regimeLabel || '全部报送体系'}`,
    `- **导出时间**：${new Date().toLocaleString('zh-CN')}`,
    `- **待整改项总数**：${items.length} 项`,
    '',
    '## 待整改清单（分歧 / 尚未包含）',
    '',
  ]
  for (const it of items) {
    const badge = formatBadge(RECONCILE_LABELS, it.reconcile_status).label
    lines.push(`### [${badge}] ${it.name} (\`${it.code}\`)`)
    lines.push(`- **监管体系**：${it.regime} · **业务板块**：${it.capability_name || '—'} · **主题**：${it.subject_theme || '—'}`)
    lines.push(`- **当前说明**：${it.summary || '待补充业务确认'}`)
    lines.push('')
  }
  return lines.join('\n')
}

export default function VersionsPage() {
  const navigate = useNavigate()
  const [agencyFilter, setAgencyFilter] = useState('ALL')
  const [regimeFilter, setRegimeFilter] = useState('ALL')
  const [activeTab, setActiveTab] = useState('feed')

  // Tab 1: 变更流
  const [assetKind, setAssetKind] = useState('ALL')
  const [revisionKind, setRevisionKind] = useState('ALL')
  const [searchFeed, setSearchFeed] = useState('')
  const [revData, setRevData] = useState({ summary: { total: 0, wording_edits: 0, confirmations: 0, assets_touched: 0 }, revisions: [] })
  const [revLoading, setRevLoading] = useState(true)

  // Tab 2 & 3: 对账分歧与跨产品交接
  const [ledger, setLedger] = useState({ summary: { VERIFIED: 0, CONSISTENT: 0, DIVERGENT: 0, UNREALIZED: 0 }, items: [] })
  const [ledgerLoading, setLedgerLoading] = useState(true)
  const [gapFilter, setGapFilter] = useState('GAPS')
  const [gapSearch, setGapSearch] = useState('')
  const [handoff, setHandoff] = useState(null)
  const [busyId, setBusyId] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  const availableRegimes = useMemo(() => {
    if (agencyFilter === 'ALL') {
      return BANK_AGENCIES.flatMap((a) => a.regimes)
    }
    return BANK_AGENCIES.find((a) => a.code === agencyFilter)?.regimes || []
  }, [agencyFilter])

  const loadAll = useCallback(async () => {
    setRevLoading(true)
    setLedgerLoading(true)
    setError('')
    try {
      const scope = {
        agency: agencyFilter !== 'ALL' ? agencyFilter : undefined,
        regime: regimeFilter !== 'ALL' ? regimeFilter : undefined,
      }
      const [revRes, ledRes, handoffRes] = await Promise.all([
        listAssetRevisions({ ...scope, assetKind, revisionKind, limit: 120 }),
        listReconcileLedger(scope),
        exportRuleStudioHandoff(scope),
      ])
      setRevData(revRes || { summary: { total: 0, wording_edits: 0, confirmations: 0, assets_touched: 0 }, revisions: [] })
      setLedger(ledRes || { summary: { VERIFIED: 0, CONSISTENT: 0, DIVERGENT: 0, UNREALIZED: 0 }, items: [] })
      setHandoff(handoffRes)
    } catch (err) {
      setError(err.message || '加载变更与对账台账失败')
    } finally {
      setRevLoading(false)
      setLedgerLoading(false)
    }
  }, [agencyFilter, regimeFilter, assetKind, revisionKind])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  async function handleQuickConfirm(item) {
    setBusyId(item.id)
    setError('')
    setNotice('')
    try {
      const payload = {
        expected_version: item.current_version || 1,
        confirmer: '业务复核员',
        comment: '在变更与对账中心确认业务口径',
      }
      if (item.asset_kind === 'RULE') {
        await confirmRule(item.id, payload)
      } else {
        await confirmCaliber(item.id, payload)
      }
      setNotice(`已确认业务资产「${item.name}」并自动写入变更记录。`)
      await loadAll()
    } catch (err) {
      setError(err.message || '确认业务资产失败')
    } finally {
      setBusyId('')
    }
  }

  function handleDownloadHandoffJson() {
    if (!handoff) return
    const blob = new Blob([JSON.stringify(handoff, null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `rule-studio-handoff-${agencyFilter.toLowerCase()}.json`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    setNotice(`已导出 ${handoff.total_items} 条整改条款交接包 (.json)，可直接导入 Data Rule Studio 装配校验规则。`)
  }

  function handleDownloadReviewMd() {
    const gapItems = (ledger.items || []).filter((i) => ['DIVERGENT', 'UNREALIZED'].includes(i.reconcile_status))
    const md = buildReconcileReviewMarkdown(
      gapItems,
      agencyMeta(agencyFilter).label,
      regimeFilter === 'ALL' ? '全部报送体系' : regimeFilter,
    )
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `reconcile-gap-review-${agencyFilter.toLowerCase()}.md`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    setNotice('已导出监管制度对账与业务口径整改评审报告 (.md)。')
  }

  const filteredRevisions = useMemo(() => {
    const q = searchFeed.trim().toLowerCase()
    const list = revData.revisions || []
    if (!q) return list
    return list.filter((r) =>
      `${r.asset_code} ${r.asset_title} ${r.capability_name} ${r.regime} ${r.editor} ${r.reason}`.toLowerCase().includes(q),
    )
  }, [revData.revisions, searchFeed])

  const filteredGaps = useMemo(() => {
    const q = gapSearch.trim().toLowerCase()
    return (ledger.items || []).filter((it) => {
      if (gapFilter === 'GAPS' && !['DIVERGENT', 'UNREALIZED'].includes(it.reconcile_status)) return false
      if (gapFilter === 'DIVERGENT' && it.reconcile_status !== 'DIVERGENT') return false
      if (gapFilter === 'UNREALIZED' && it.reconcile_status !== 'UNREALIZED') return false
      if (!q) return true
      return `${it.code} ${it.name} ${it.capability_name} ${it.regime} ${it.subject_theme} ${it.summary}`.toLowerCase().includes(q)
    })
  }, [ledger.items, gapFilter, gapSearch])

  const alignedCount = (ledger.summary.VERIFIED || 0) + (ledger.summary.CONSISTENT || 0)
  const gapTotal = (ledger.summary.DIVERGENT || 0) + (ledger.summary.UNREALIZED || 0)

  return (
    <div className="dbc-gov-page">
      <div className="dbc-page-header">
        <div>
          <span className="dbc-eyebrow">资产治理 · 修订动态、分歧闭环与跨产品交接</span>
          <h1 className="dbc-page-title">业务变更与对账整改中心</h1>
          <p className="dbc-page-desc">
            集中追踪业务专家对规则/指标口径的人工修订与确权记录，按商业银行监管机构闭环跟进多口径分歧与未落地条款，并一键交接至 Data Rule Studio。
          </p>
        </div>
        <div className="dbc-header-actions">
          <button type="button" className="btn btn-sm" onClick={handleDownloadReviewMd}>
            导出整改评审报告 (.md)
          </button>
          <button type="button" className="btn btn-sm btn-primary" onClick={() => navigate('/admin/audit')}>
            前往审计中心 →
          </button>
        </div>
      </div>

      {/* 商业银行两级监管切换条 */}
      <section className="dbc-taxonomy-bar" aria-label="按商业银行监管机构与报表体系筛选">
        <div className="dbc-tax-row">
          <span className="dbc-tax-label">监管机构</span>
          <div className="dbc-tax-pills">
            <button
              type="button"
              className={`dbc-agency-pill ${agencyFilter === 'ALL' ? 'active' : ''}`}
              onClick={() => { setAgencyFilter('ALL'); setRegimeFilter('ALL') }}
            >
              全部机构
            </button>
            {BANK_AGENCIES.map((ag) => (
              <button
                key={ag.code}
                type="button"
                className={`dbc-agency-pill ${agencyFilter === ag.code ? 'active' : ''}`}
                title={ag.hint}
                onClick={() => { setAgencyFilter(ag.code); setRegimeFilter('ALL') }}
              >
                {ag.label}
              </button>
            ))}
          </div>
        </div>
        <div className="dbc-tax-row">
          <span className="dbc-tax-label">监管报表</span>
          <div className="dbc-tax-pills">
            <button
              type="button"
              className={`dbc-regime-pill ${regimeFilter === 'ALL' ? 'active' : ''}`}
              onClick={() => setRegimeFilter('ALL')}
            >
              {agencyFilter === 'ALL' ? '全部体系' : `${agencyMeta(agencyFilter).shortLabel}全部体系`}
            </button>
            {availableRegimes.map((reg) => (
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

      {error && (
        <div className="dbc-alert is-error">
          <span>{error}</span>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setError('')}>关闭</button>
        </div>
      )}
      {notice && (
        <div className="dbc-alert is-ok">
          <span>{notice}</span>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setNotice('')}>关闭</button>
        </div>
      )}

      {/* 顶部 4 张 KPI 卡 */}
      <section className="dbc-kpi-grid">
        <article className="dbc-kpi-card">
          <div className="dbc-kpi-head">
            <span>人工修订与业务确认</span>
            <span className="dbc-pill tone-green">{revData.summary.confirmations} 次确认</span>
          </div>
          <strong className="dbc-kpi-value">{revData.summary.wording_edits} 次释义修订</strong>
          <small className="dbc-kpi-sub">共累计 {revData.summary.total} 条历史留痕，涉及 {revData.summary.assets_touched} 项资产</small>
        </article>

        <article className="dbc-kpi-card is-safe">
          <div className="dbc-kpi-head">
            <span>逻辑与制度一致</span>
            <span className="dbc-pill tone-green">已落地</span>
          </div>
          <strong className="dbc-kpi-value">{alignedCount} 项</strong>
          <small className="dbc-kpi-sub">
            双源互证 {ledger.summary.VERIFIED || 0} 项 · 已有逻辑支撑 {ledger.summary.CONSISTENT || 0} 项
          </small>
        </article>

        <article className="dbc-kpi-card">
          <div className="dbc-kpi-head">
            <span>存在多套口径分歧</span>
            <span className="dbc-pill tone-amber">需业务裁决</span>
          </div>
          <strong className="dbc-kpi-value">{ledger.summary.DIVERGENT || 0} 项</strong>
          <small className="dbc-kpi-sub">不同业务流程或制度条款之间存在口径差异，需人工统一口径</small>
        </article>

        <article className="dbc-kpi-card">
          <div className="dbc-kpi-head">
            <span>制度提及尚未包含</span>
            <span className="dbc-pill tone-purple">可转 Rule Studio</span>
          </div>
          <strong className="dbc-kpi-value">{ledger.summary.UNREALIZED || 0} 项</strong>
          <small className="dbc-kpi-sub">可一键导出标准化交接包，在 Data Rule Studio 装配校验规则</small>
        </article>
      </section>

      <div className="dbc-card">
        <div className="dbc-sub-tabs">
          <button type="button" className={activeTab === 'feed' ? 'active' : ''} onClick={() => setActiveTab('feed')}>
            1. 业务资产变更动态流 ({revData.summary.total})
          </button>
          <button type="button" className={activeTab === 'gaps' ? 'active' : ''} onClick={() => setActiveTab('gaps')}>
            2. 分歧与未落地闭环台账 ({gapTotal})
          </button>
          <button type="button" className={activeTab === 'handoff' ? 'active' : ''} onClick={() => setActiveTab('handoff')}>
            3. 跨产品交接：导出至 Rule Studio ({handoff?.total_items ?? 0})
          </button>
        </div>

        {/* Tab 1: 变更动态流 */}
        {activeTab === 'feed' && (
          <div className="dbc-tab-body">
            <div className="dbc-inline-toolbar">
              <input
                className="input"
                type="search"
                placeholder="搜索资产名称、编号、业务板块、操作人或变更说明…"
                value={searchFeed}
                onChange={(e) => setSearchFeed(e.target.value)}
              />
              <SelectDropdown ariaLabel="按资产类型筛选" size="sm" minWidth={150} value={assetKind} options={ASSET_KIND_OPTIONS} onChange={setAssetKind} />
              <SelectDropdown ariaLabel="按变更类型筛选" size="sm" minWidth={165} value={revisionKind} options={REVISION_KIND_OPTIONS} onChange={setRevisionKind} />
            </div>

            {revLoading ? (
              <div className="dbc-empty">正在加载业务资产变更记录…</div>
            ) : filteredRevisions.length === 0 ? (
              <div className="dbc-empty">
                当前范围内暂无人工修订或业务确认记录。前往「业务资产」补充业务释义或确认口径后，每次修改都会保留前后对照 Diff。
              </div>
            ) : (
              <div className="dbc-rev-list">
                {filteredRevisions.map((rev) => {
                  const rm = REVISION_LABELS[rev.revision_kind] || { label: rev.revision_kind, tone: 'blue' }
                  const agMeta = agencyMeta(rev.agency || agencyForRegime(rev.regime))
                  const oldObj = rev.old_value || {}
                  const newObj = rev.new_value || {}
                  const diffKeys = Object.keys(newObj).filter((k) => String(oldObj[k] ?? '') !== String(newObj[k] ?? ''))
                  return (
                    <article className="dbc-rev-card" key={rev.id}>
                      <div className="dbc-rev-top">
                        <span className="dbc-pill tone-slate">{agMeta.shortLabel} · {rev.regime}</span>
                        <span className={`dbc-pill tone-${rm.tone}`}>{rm.label}</span>
                        <strong>{rev.asset_title}</strong>
                        <code>{rev.asset_code}</code>
                        <span className="dbc-muted">操作人：{rev.editor || '系统'}</span>
                        <time className="dbc-event-time">{formatTime(rev.created_at)}</time>
                      </div>
                      {rev.reason && <p className="dbc-rev-reason">变更说明：{rev.reason}</p>}
                      {diffKeys.length > 0 && (
                        <div className="dbc-rev-diff-list">
                          {diffKeys.map((key) => (
                            <div className="dbc-rev-diff-row" key={key}>
                              <span className="dbc-rev-field">{EDIT_FIELD_LABELS[key] || key}</span>
                              <del className="dbc-diff-old">{String(oldObj[key] ?? '（空）')}</del>
                              <span>→</span>
                              <ins className="dbc-diff-new">{String(newObj[key] ?? '（空）')}</ins>
                            </div>
                          ))}
                        </div>
                      )}
                    </article>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {/* Tab 2: 分歧与未落地闭环台账 */}
        {activeTab === 'gaps' && (
          <div className="dbc-tab-body">
            <div className="dbc-inline-toolbar">
              <input
                className="input"
                type="search"
                placeholder="搜索待整改资产名称、业务板块、金融主题或说明…"
                value={gapSearch}
                onChange={(e) => setGapSearch(e.target.value)}
              />
              <SelectDropdown ariaLabel="按对账状态筛选" size="sm" minWidth={210} value={gapFilter} options={GAP_STATUS_OPTIONS} onChange={setGapFilter} />
            </div>

            {ledgerLoading ? (
              <div className="dbc-empty">正在加载制度对账台账…</div>
            ) : filteredGaps.length === 0 ? (
              <div className="dbc-empty">当前监管范围内暂无需要整改的分歧或未落地项。</div>
            ) : (
              <div className="dbc-gap-list">
                {filteredGaps.map((item) => {
                  const recBadge = formatBadge(RECONCILE_LABELS, item.reconcile_status)
                  const ag = agencyMeta(item.agency || agencyForRegime(item.regime))
                  return (
                    <article className="dbc-gap-card" key={`${item.asset_kind}-${item.id}`}>
                      <div className="dbc-gap-head">
                        <div className="dbc-gap-tags">
                          <span className="dbc-pill tone-slate">{ag.shortLabel} · {item.regime}</span>
                          <span className={`dbc-pill tone-${recBadge.tone}`}>{recBadge.label}</span>
                          <span className="dbc-pill tone-blue">{item.asset_kind === 'RULE' ? '处理规则' : '指标口径'}</span>
                          {item.subject_theme && <span className="dbc-node-chip">{item.subject_theme}</span>}
                        </div>
                        <div className="dbc-gap-actions">
                          {item.status !== 'CONFIRMED' ? (
                            <button
                              type="button"
                              className="btn btn-sm btn-primary"
                              disabled={busyId === item.id}
                              onClick={() => handleQuickConfirm(item)}
                            >
                              {busyId === item.id ? '确认中…' : '人工确权确认'}
                            </button>
                          ) : (
                            <span className="dbc-pill tone-green">✓ 已人工确认</span>
                          )}
                        </div>
                      </div>
                      <h3 className="dbc-gap-title">
                        {item.name} <code>{item.code}</code>
                      </h3>
                      <p className="dbc-gap-summary">{item.summary || '待结合监管制度原文补充口径说明。'}</p>
                      <div className="dbc-gap-foot">
                        <span>所属业务板块：{item.capability_name || '—'}</span>
                        <span>系统实证 {item.logic_backing_count} 处 · 制度条款 {item.clause_backing_count} 条</span>
                      </div>
                    </article>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {/* Tab 3: 跨产品协同交接至 Rule Studio */}
        {activeTab === 'handoff' && (
          <div className="dbc-tab-body">
            <div className="dbc-handoff-banner">
              <div>
                <h3>产品家族闭环：从「发现缺口」到「规则装配交付」</h3>
                <p>
                  Data Biz Copilot 负责在业务与制度对账中发现<strong>「尚未系统包含 (UNREALIZED)」</strong>或
                  <strong>「多套口径分歧 (DIVERGENT)」</strong>项；导出的交接包已按商业银行标准监管编码（<code>YBT / EAST / 1104 / SAFE_BIZ / PBOC_STAT</code>）映射，
                  可直接交给 <strong>Data Rule Studio</strong> 编译为经 Oracle 23 沙箱 100% 覆盖验证的生产校验 SQL 部署包。
                </p>
              </div>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!handoff || !handoff.total_items}
                onClick={handleDownloadHandoffJson}
              >
                导出 Rule Studio 交接包 (.json)
              </button>
            </div>

            {!handoff?.items?.length ? (
              <div className="dbc-empty">当前筛选范围内没有需要转交 Data Rule Studio 的未落地或分歧条款。</div>
            ) : (
              <div className="dbc-handoff-list">
                {handoff.items.map((it) => (
                  <div className="dbc-handoff-row" key={it.asset_code}>
                    <div>
                      <div className="dbc-gap-tags" style={{ marginBottom: 4 }}>
                        <span className="dbc-pill tone-purple">Rule Studio: {it.rule_studio_regulator}</span>
                        <span className="dbc-pill tone-slate">{it.regime}</span>
                        <span className={`dbc-pill tone-${it.reconcile_status === 'DIVERGENT' ? 'amber' : 'slate'}`}>
                          {formatBadge(RECONCILE_LABELS, it.reconcile_status).label}
                        </span>
                      </div>
                      <strong>{it.title}</strong> <code>{it.asset_code}</code>
                      <p className="dbc-muted" style={{ margin: '4px 0 0' }}>{it.business_summary}</p>
                    </div>
                    <span className="dbc-pill tone-blue">附带 {it.regulatory_clauses?.length || 0} 条制度原文</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
