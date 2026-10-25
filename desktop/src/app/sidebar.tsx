import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { useState } from 'react'
import { Link, useMatch } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useSettings, useSetupStatus } from '@/lib/api/hooks'
import { cn } from '@/lib/utils'
import { NAV, type NavItem } from './nav'

const KEY = 'reachout.sidebar.collapsed'

function readCollapsed(): boolean {
  try { return localStorage.getItem(KEY) === '1' } catch { return false }
}

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggle = () => {
    setCollapsed((c) => {
      try { localStorage.setItem(KEY, c ? '0' : '1') } catch { /* ignore */ }
      return !c
    })
  }

  return (
    <aside
      className={cn(
        'bg-sidebar text-sidebar-foreground border-sidebar-border flex shrink-0 flex-col border-r transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-60',
      )}
      aria-label="Sidebar"
    >
      <div className="drag h-12 shrink-0" />
      <nav className="flex-1 space-y-0.5 px-2" aria-label="Main">
        {NAV.map((item) => <NavEntry key={item.id} item={item} collapsed={collapsed} />)}
      </nav>
      <SidebarFooter collapsed={collapsed} />
      <div className="border-sidebar-border border-t p-2">
        <Button
          variant="ghost"
          size={collapsed ? 'icon' : 'sm'}
          className="no-drag w-full justify-start"
          onClick={toggle}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <PanelLeftOpen aria-hidden /> : <PanelLeftClose aria-hidden />}
          {collapsed ? null : 'Collapse'}
        </Button>
      </div>
    </aside>
  )
}

function NavEntry({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const Icon = item.icon
  const base = 'flex h-9 items-center gap-3 rounded-lg px-2.5 text-sm font-medium outline-none'

  if (item.milestone !== null) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            role="link"
            aria-disabled="true"
            tabIndex={-1}
            className={cn(base, 'text-muted-foreground cursor-not-allowed opacity-60', collapsed && 'justify-center px-0')}
          >
            <Icon className="size-4 shrink-0" aria-hidden />
            {collapsed ? <span className="sr-only">{item.label} (coming soon)</span> : (
              <>
                <span className="flex-1">{item.label}</span>
                <Badge variant="outline" className="text-[10px]">Soon</Badge>
              </>
            )}
          </div>
        </TooltipTrigger>
        <TooltipContent side="right">{item.label}: coming in milestone {item.milestone}</TooltipContent>
      </Tooltip>
    )
  }

  return <ActiveNavEntry item={item} collapsed={collapsed} base={base} />
}

/** Separate component so the route match hook is not called conditionally. */
function ActiveNavEntry({ item, collapsed, base }: { item: NavItem; collapsed: boolean; base: string }) {
  const Icon = item.icon
  const active = useMatch({ path: item.to, end: false }) !== null
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          to={item.to}
          aria-current={active ? 'page' : undefined}
          className={cn(base, 'focus-visible:ring-ring focus-visible:ring-2',
            active ? 'bg-sidebar-accent text-sidebar-accent-foreground' : 'hover:bg-sidebar-accent/60',
            collapsed && 'justify-center px-0')}
        >
          <Icon className="size-4 shrink-0" aria-hidden />
          {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
        </Link>
      </TooltipTrigger>
      {collapsed ? <TooltipContent side="right">{item.label}</TooltipContent> : null}
    </Tooltip>
  )
}

function SidebarFooter({ collapsed }: { collapsed: boolean }) {
  const status = useSetupStatus()
  const settings = useSettings()
  const ollama = status.data?.checks.find((c) => c.id === 'ollama')
  const model = status.data?.checks.find((c) => c.id === 'model')
  const ready = ollama?.status === 'ok' && model?.status === 'ok'
  const label = ollama === undefined ? 'Checking Ollama…' : ollama.status !== 'ok' ? 'Ollama offline' : model?.status !== 'ok' ? 'Model missing' : 'Ollama ready'
  const cap = settings.data?.settings.daily_cap

  return (
    <div className="space-y-2 px-3 pb-3 text-xs">
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex items-center gap-2" role="status" data-testid="ollama-status">
            <span
              className={cn('size-2 shrink-0 rounded-full', ready ? 'bg-emerald-500' : ollama === undefined ? 'bg-muted-foreground' : 'bg-destructive')}
              aria-hidden
            />
            {collapsed ? <span className="sr-only">{label}</span> : <span className="truncate">{label}</span>}
          </div>
        </TooltipTrigger>
        <TooltipContent side="right">{label}{status.data ? ` · ${status.data.model}` : ''}</TooltipContent>
      </Tooltip>

      {collapsed ? null : (
        <>
          <div className="text-muted-foreground flex justify-between" data-testid="sends-today">
            <span>Sent today</span>
            <span className="font-mono">0 / {cap ?? '…'}</span>
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <label className="text-muted-foreground flex items-center justify-between gap-2 opacity-60">
                <span>Pause all sending</span>
                <Switch disabled aria-label="Pause all sending (available after milestone 4)" />
              </label>
            </TooltipTrigger>
            <TooltipContent side="right">Available once sending is built (milestone 4)</TooltipContent>
          </Tooltip>
        </>
      )}
    </div>
  )
}
