import { useEffect } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { SettingsPage } from '@/features/settings/settings-page'
import { BackendBanner } from './backend-banner'
import { CommandPalette } from './command-palette'
import { Sidebar } from './sidebar'

export function AppShell() {
  const navigate = useNavigate()
  const bridge = window.reachout

  useEffect(
    () => bridge?.onMenu((a) => { if (a.type === 'navigate') navigate(a.route) }),
    [bridge, navigate],
  )

  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="bg-background flex min-w-0 flex-1 flex-col">
        <div className="drag h-12 shrink-0" />
        <BackendBanner />
        <div className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/settings" replace />} />
          </Routes>
        </div>
      </main>
      <CommandPalette />
    </div>
  )
}
