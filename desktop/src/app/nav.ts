import { Compass, Inbox, LayoutDashboard, PenLine, Send, Settings, Users, type LucideIcon } from 'lucide-react'

export interface NavItem {
  id: string
  label: string
  to: string
  icon: LucideIcon
  /** Milestone that delivers the screen; null means it exists today. */
  milestone: number | null
}

export const NAV: NavItem[] = [
  { id: 'dashboard', label: 'Dashboard', to: '/dashboard', icon: LayoutDashboard, milestone: 5 },
  { id: 'discover', label: 'Discover', to: '/discover', icon: Compass, milestone: 2 },
  { id: 'leads', label: 'Leads', to: '/leads', icon: Users, milestone: 2 },
  { id: 'compose', label: 'Compose', to: '/compose', icon: PenLine, milestone: 3 },
  { id: 'campaigns', label: 'Campaigns', to: '/campaigns', icon: Send, milestone: 4 },
  { id: 'inbox', label: 'Inbox', to: '/inbox', icon: Inbox, milestone: 5 },
  { id: 'settings', label: 'Settings', to: '/settings', icon: Settings, milestone: null },
]
