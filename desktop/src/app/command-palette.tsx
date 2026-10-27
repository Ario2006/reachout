import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Compass, FolderOpen, MonitorCog, RefreshCw, ScrollText, Search, Settings, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator,
} from '@/components/ui/command'
import { api } from '@/lib/api/client'
import { qk } from '@/lib/api/hooks'
import { useTheme } from '@/lib/theme'
import { SETTINGS_TABS } from '@/features/settings/settings-page'

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { preference, setPreference } = useTheme()
  const bridge = window.reachout
  const term = typed.trim()
  const found = useQuery({
    queryKey: ['palette-leads', term],
    queryFn: () => api.get('/leads', { query: { q: term, limit: 5 } }),
    enabled: open && term.length >= 2,
    staleTime: 10_000,
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(
    () => bridge?.onMenu((a) => { if (a.type === 'command-palette') setOpen(true) }),
    [bridge],
  )

  const run = (fn: () => void) => () => { setOpen(false); fn() }
  const nextTheme = preference === 'system' ? 'light' : preference === 'light' ? 'dark' : 'system'

  return (
    <CommandDialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setTyped("") }} title="Command palette" description="Jump to a screen or run an action">
      <Command>
      <CommandInput placeholder="Type a command, or search leads by name, email or company…" value={typed} onValueChange={setTyped} />
      <CommandList>
        <CommandEmpty>No matching commands.</CommandEmpty>
        {term.length >= 2 && (found.data?.items.length ?? 0) > 0 ? (
          <CommandGroup heading="Leads">
            {found.data?.items.map((l) => (
              <CommandItem key={l.id} value={`lead ${l.name ?? ''} ${l.email} ${l.company ?? ''} ${term}`} onSelect={run(() => navigate(`/leads?q=${encodeURIComponent(l.email)}`))}>
                <Users aria-hidden /> <span className="truncate">{l.name || l.email}</span>
                <span className="text-muted-foreground ml-2 truncate text-xs">{l.name ? l.email : ''} {l.company ? `· ${l.company}` : ''}</span>
              </CommandItem>
            ))}
            <CommandItem value={`search all leads for ${term}`} onSelect={run(() => navigate(`/leads?q=${encodeURIComponent(term)}`))}>
              <Search aria-hidden /> Search all leads for “{term}”
            </CommandItem>
          </CommandGroup>
        ) : null}
        <CommandGroup heading="Go to">
          <CommandItem onSelect={run(() => navigate('/discover'))}><Compass aria-hidden /> Discover: new search</CommandItem>
          <CommandItem onSelect={run(() => navigate('/leads'))}><Users aria-hidden /> Leads</CommandItem>
          <CommandItem onSelect={run(() => navigate('/settings'))}><Settings aria-hidden /> Settings</CommandItem>
          {SETTINGS_TABS.filter((t) => t.value !== 'account').map((t) => (
            <CommandItem key={t.value} onSelect={run(() => navigate(`/settings?tab=${t.value}`))}>
              <Settings aria-hidden /> Settings: {t.label}
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Actions">
          <CommandItem onSelect={run(() => setPreference(nextTheme))}>
            <MonitorCog aria-hidden /> Switch theme (now {preference}, next {nextTheme})
          </CommandItem>
          <CommandItem onSelect={run(() => {
            void qc.invalidateQueries({ queryKey: qk.setupStatus })
            void qc.invalidateQueries({ queryKey: qk.models })
          })}>
            <RefreshCw aria-hidden /> Re-check Ollama
          </CommandItem>
          {bridge ? (
            <>
              <CommandItem onSelect={run(() => void bridge.openLogs())}><ScrollText aria-hidden /> Show logs</CommandItem>
              <CommandItem onSelect={run(() => void bridge.openDataFolder())}><FolderOpen aria-hidden /> Open data folder</CommandItem>
            </>
          ) : null}
        </CommandGroup>
      </CommandList>
      </Command>
    </CommandDialog>
  )
}
