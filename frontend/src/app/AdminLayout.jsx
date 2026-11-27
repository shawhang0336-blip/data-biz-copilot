import React, { useCallback, useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { getPanorama } from '../api/catalog.js'
import { listSources } from '../api/ingestion.js'
import { getAuditOverview } from '../api/platform.js'
import BackendHealthPill from '../components/BackendHealthPill.jsx'
import { useTheme } from '../context/ThemeContext.jsx'
import {
  ArrowLeftIcon,
  BrandMark,
  CheckShieldIcon,
  ChevronRightIcon,
  ClockIcon,
  LayersIcon,
  SlidersIcon,
  UploadIcon,
} from '../utils/icons.jsx'

const ROUTE_LABELS = {
  '/admin/sources': '知识材料',
  '/admin/assets': '业务资产',
  '/admin/versions': '变更与对账',
  '/admin/audit': '审计中心',
  '/admin/settings': '系统配置',
}

export default function AdminLayout() {
  const location = useLocation()
  const { theme, toggleTheme } = useTheme()
  const [stats, setStats] = useState({
    capability_count: 0,
    covered_capabilities: 0,
    rule_count: 0,
    caliber_count: 0,
  })
  const [sourcesCount, setSourcesCount] = useState(0)
  const [auditRunsCount, setAuditRunsCount] = useState(0)
  const [gapCount, setGapCount] = useState(0)

  const refreshStats = useCallback(async () => {
    try {
      const [pano, srcList, auditOv] = await Promise.all([
        getPanorama().catch(() => null),
        listSources().catch(() => null),
        getAuditOverview({ limit: 20 }).catch(() => null),
      ])
      if (pano?.stats) setStats(pano.stats)
      if (pano?.reconcile?.summary) {
        setGapCount((pano.reconcile.summary.DIVERGENT || 0) + (pano.reconcile.summary.UNREALIZED || 0))
      }
      if (Array.isArray(srcList)) setSourcesCount(srcList.length)
      if (auditOv?.summary) setAuditRunsCount(auditOv.summary.total_runs || 0)
    } catch {
      // 忽略首次空库时的统计加载异常
    }
  }, [])

  useEffect(() => {
    refreshStats()
  }, [refreshStats])

  const currentTitle = ROUTE_LABELS[location.pathname] || '知识材料'

  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <div className="admin-brand-row">
          <BrandMark size={28} />
          <div>
            <div className="admin-brand-name">Data Biz Copilot</div>
            <div className="admin-brand-tag">银行治理管理台</div>
          </div>
        </div>

        <nav className="admin-nav" aria-label="平台管理导航">
          <NavLink
            to="/admin/sources"
            className={({ isActive }) => `admin-nav-link ${isActive ? 'active' : ''}`}
          >
            <span className="admin-nav-left">
              <UploadIcon size={16} />
              <span>知识材料</span>
            </span>
            <span className="admin-nav-count">{sourcesCount}</span>
          </NavLink>

          <NavLink
            to="/admin/assets"
            className={({ isActive }) => `admin-nav-link ${isActive ? 'active' : ''}`}
          >
            <span className="admin-nav-left">
              <LayersIcon size={16} />
              <span>业务资产</span>
            </span>
            <span className="admin-nav-count">{stats.capability_count || 0}</span>
          </NavLink>

          <NavLink
            to="/admin/versions"
            className={({ isActive }) => `admin-nav-link ${isActive ? 'active' : ''}`}
          >
            <span className="admin-nav-left">
              <ClockIcon size={16} />
              <span>变更与对账</span>
            </span>
            {gapCount > 0 && <span className="admin-nav-count is-warn">{gapCount}</span>}
          </NavLink>

          <NavLink
            to="/admin/audit"
            className={({ isActive }) => `admin-nav-link ${isActive ? 'active' : ''}`}
          >
            <span className="admin-nav-left">
              <CheckShieldIcon size={16} />
              <span>审计中心</span>
            </span>
            <span className="admin-nav-count">{auditRunsCount}</span>
          </NavLink>

          <NavLink
            to="/admin/settings"
            className={({ isActive }) => `admin-nav-link ${isActive ? 'active' : ''}`}
          >
            <span className="admin-nav-left">
              <SlidersIcon size={16} />
              <span>系统配置</span>
            </span>
          </NavLink>
        </nav>

        <Link to="/" className="admin-back-link">
          <ArrowLeftIcon size={15} />
          <span>返回业务问答</span>
        </Link>
      </aside>

      <div className="admin-body">
        <header className="admin-topbar">
          <div className="breadcrumb">
            <span>平台管理</span>
            <ChevronRightIcon size={14} />
            <span className="breadcrumb-current">{currentTitle}</span>
          </div>
          <div className="admin-topbar-actions">
            <BackendHealthPill />
            <button
              type="button"
              className="btn btn-sm"
              onClick={toggleTheme}
              title="切换浅色/深色外观"
            >
              {theme === 'dark' ? '浅色外观' : '深色外观'}
            </button>
            <Link to="/" className="btn btn-sm btn-ghost">
              <ArrowLeftIcon size={14} />
              <span>返回业务问答</span>
            </Link>
          </div>
        </header>

        <main className="admin-main">
          <div className="admin-container">
            <Outlet context={{ refreshStats, stats, sourcesCount }} />
          </div>
        </main>
      </div>
    </div>
  )
}
