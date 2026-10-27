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
  { id: 'dashboard', label: 'Dashboard', to: '/dashboard', icon: LayoutDashboard, milestone: null },
  { id: 'discover', label: 'Discover', to: '/discover', icon: Compass, milestone: null },
  { id: 'leads', label: 'Leads', to: '/leads', icon: Users, milestone: null },
  { id: 'compose', label: 'Compose', to: '/compose', icon: PenLine, milestone: null },
  { id: 'campaigns', label: 'Campaigns', to: '/campaigns', icon: Send, milestone: null },
  { id: 'inbox', label: 'Inbox', to: '/inbox', icon: Inbox, milestone: null },
  { id: 'settings', label: 'Settings', to: '/settings', icon: Settings, milestone: null },
]
