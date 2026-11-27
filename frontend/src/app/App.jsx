import React, { Suspense, lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../context/ThemeContext.jsx'
import CopilotPage from '../features/copilot/CopilotPage.jsx'
import AdminLayout from './AdminLayout.jsx'

const SourcesPage = lazy(() => import('../features/ingestion/SourcesPage.jsx'))
const AssetsPage = lazy(() => import('../features/catalog/AssetsPage.jsx'))
const VersionsPage = lazy(() => import('../features/catalog/VersionsPage.jsx'))
const AuditCenterPage = lazy(() => import('../features/platform/AuditCenterPage.jsx'))
const SettingsPage = lazy(() => import('../features/platform/SettingsPage.jsx'))

function PageFallback() {
  return <div className="dbc-card dbc-empty">正在加载工作台模块…</div>
}

export default function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/" element={<CopilotPage />} />
            <Route path="/copilot" element={<CopilotPage />} />

            <Route path="/admin" element={<AdminLayout />}>
              <Route index element={<Navigate to="/admin/sources" replace />} />
              <Route path="sources" element={<SourcesPage />} />
              <Route path="assets" element={<AssetsPage />} />
              <Route path="versions" element={<VersionsPage />} />
              <Route path="audit" element={<AuditCenterPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>

            <Route path="/sources" element={<Navigate to="/admin/sources" replace />} />
            <Route path="/assets" element={<Navigate to="/admin/assets" replace />} />
            <Route path="/versions" element={<Navigate to="/admin/versions" replace />} />
            <Route path="/audit" element={<Navigate to="/admin/audit" replace />} />
            <Route path="/settings" element={<Navigate to="/admin/settings" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ThemeProvider>
  )
}
