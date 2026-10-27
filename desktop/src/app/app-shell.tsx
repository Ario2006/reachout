import { useEffect } from 'react'
import { CampaignsPage } from '@/features/campaigns/campaigns-page'
import { ComposePage } from '@/features/compose/compose-page'
import { DashboardPage } from '@/features/dashboard/dashboard-page'
import { InboxPage } from '@/features/inbox/inbox-page'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { DiscoverPage } from '@/features/discover/discover-page'
import { LeadsPage } from '@/features/leads/leads-page'
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
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Routes>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/discover" element={<DiscoverPage />} />
            <Route path="/leads" element={<LeadsPage />} />
            <Route path="/compose" element={<ComposePage />} />
            <Route path="/campaigns" element={<CampaignsPage />} />
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/discover" replace />} />
          </Routes>
        </div>
      </main>
      <CommandPalette />
    </div>
  )
}
