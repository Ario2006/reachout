import { useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderOpen, Inbox, MonitorCog, PauseCircle, PlayCircle, Plus, RefreshCw, ScrollText, Search, Settings, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator, CommandShortcut,
} from '@/components/ui/command'
import { api, ApiError } from '@/lib/api/client'
import { qk } from '@/lib/api/hooks'
import { useSendingStatus, useSetPauseAll } from '@/lib/api/hooks-campaigns'
import { useCheckNow } from '@/lib/api/hooks-inbox'
import { useTheme } from '@/lib/theme'
import { SETTINGS_TABS } from '@/features/settings/settings-page'
import { NAV } from './nav'

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { preference, setPreference } = useTheme()
  const bridge = window.reachout
  const sending = useSendingStatus()
  const setPaused = useSetPauseAll()
  const checkNow = useCheckNow()
  const pausedAll = sending.data?.paused_all
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
  const failure = (fallback: string) => (e: unknown) => toast.error(e instanceof ApiError ? e.detail : fallback)
  const nextTheme = preference === 'system' ? 'light' : preference === 'light' ? 'dark' : 'system'

  return (
    <CommandDialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setTyped('') }} title="Command palette" description="Jump to a screen or run an action">
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
          {NAV.map((n, i) => (
            <CommandItem key={n.id} value={`go to ${n.label}`} onSelect={run(() => navigate(n.to))}>
              <n.icon aria-hidden /> {n.label}
              <CommandShortcut>⌘{i + 1}</CommandShortcut>
            </CommandItem>
          ))}
          {SETTINGS_TABS.filter((t) => t.value !== 'account').map((t) => (
            <CommandItem key={t.value} onSelect={run(() => navigate(`/settings?tab=${t.value}`))}>
              <Settings aria-hidden /> Settings: {t.label}
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Actions">
          <CommandItem value="new search discover" onSelect={run(() => navigate('/discover'))}><Plus aria-hidden /> New search</CommandItem>
          <CommandItem value="check inbox now" onSelect={run(() => checkNow.mutate(undefined, {
            onSuccess: () => toast.success('Checking Gmail for replies and bounces'),
            onError: failure('Could not start the inbox check'),
          }))}>
            <Inbox aria-hidden /> Check inbox now
          </CommandItem>
          {pausedAll !== true ? (
            <CommandItem value="pause sending" onSelect={run(() => setPaused.mutate(true, {
              onSuccess: () => toast.success('Sending paused for every campaign'),
              onError: failure('Could not pause sending'),
            }))}>
              <PauseCircle aria-hidden /> Pause sending
            </CommandItem>
          ) : null}
          {pausedAll !== false ? (
            <CommandItem value="resume sending" onSelect={run(() => setPaused.mutate(false, {
              onSuccess: () => toast.success('Sending resumed'),
              onError: failure('Could not resume sending'),
            }))}>
              <PlayCircle aria-hidden /> Resume sending
            </CommandItem>
          ) : null}
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
