import { useEffect, useRef } from 'react'
import { CampaignsPage } from '@/features/campaigns/campaigns-page'
import { ComposePage } from '@/features/compose/compose-page'
import { DashboardPage } from '@/features/dashboard/dashboard-page'
import { InboxPage } from '@/features/inbox/inbox-page'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { DiscoverPage } from '@/features/discover/discover-page'
import { LeadsPage } from '@/features/leads/leads-page'
import { SettingsPage } from '@/features/settings/settings-page'
import { BackendBanner } from './backend-banner'
import { CommandPalette } from './command-palette'
import { Sidebar } from './sidebar'

export function AppShell() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const bridge = window.reachout
  const main = useRef<HTMLElement>(null)
  const firstPath = useRef(pathname)
  // After a navigation (sidebar, menu or palette) move keyboard focus to the new screen; not on first load.
  useEffect(() => {
    if (pathname !== firstPath.current) main.current?.focus({ preventScroll: true })
  }, [pathname])

  useEffect(
    () => bridge?.onMenu((a) => { if (a.type === 'navigate') navigate(a.route) }),
    [bridge, navigate],
  )

  return (
    <div className="flex h-full">
      <a href="#main" className="bg-background text-foreground focus-visible:ring-ring sr-only z-50 rounded-md px-3 py-2 text-sm focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus-visible:ring-2"
        onClick={(e) => { e.preventDefault(); main.current?.focus() }}>
        Skip to content
      </a>
      <Sidebar />
      <main id="main" ref={main} tabIndex={-1} aria-label="Content" className="bg-background flex min-w-0 flex-1 flex-col outline-none">
        <div className="drag h-12 shrink-0" />
        <BackendBanner />
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div key={pathname} className="route-fade h-full">
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
        </div>
      </main>
      <CommandPalette />
    </div>
  )
}
