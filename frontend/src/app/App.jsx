import React from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AssetsPage from '../features/catalog/AssetsPage.jsx'
import CopilotPage from '../features/copilot/CopilotPage.jsx'
import SourcesPage from '../features/ingestion/SourcesPage.jsx'
import SettingsPage from '../features/platform/SettingsPage.jsx'
import AdminLayout from './AdminLayout.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<CopilotPage />} />
        <Route path="/copilot" element={<CopilotPage />} />

        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="/admin/sources" replace />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="assets" element={<AssetsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>

        <Route path="/sources" element={<Navigate to="/admin/sources" replace />} />
        <Route path="/assets" element={<Navigate to="/admin/assets" replace />} />
        <Route path="/settings" element={<Navigate to="/admin/settings" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
