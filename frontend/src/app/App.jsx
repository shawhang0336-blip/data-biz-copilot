import React from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import SourcesPage from '../features/ingestion/SourcesPage.jsx'
import AssetsPage from '../features/catalog/AssetsPage.jsx'
import CopilotPage from '../features/copilot/CopilotPage.jsx'
import SettingsPage from '../features/platform/SettingsPage.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <header className="topbar">
          <div className="brand">Data Copilot Agent · 业务逻辑资产台</div>
          <nav className="nav-links">
            <NavLink to="/sources" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>1. 双源接入</NavLink>
            <NavLink to="/assets" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>2. 业务资产层</NavLink>
            <NavLink to="/copilot" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>3. 业务问答台</NavLink>
            <NavLink to="/settings" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>设置</NavLink>
          </nav>
        </header>
        <main className="page-wrap">
          <Routes>
            <Route path="/sources" element={<SourcesPage />} />
            <Route path="/assets" element={<AssetsPage />} />
            <Route path="/copilot" element={<CopilotPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/copilot" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
