import { useQueryClient } from '@tanstack/react-query'
import { FolderOpen, MonitorCog, RefreshCw, ScrollText, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator,
} from '@/components/ui/command'
import { qk } from '@/lib/api/hooks'
import { useTheme } from '@/lib/theme'
import { SETTINGS_TABS } from '@/features/settings/settings-page'

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { preference, setPreference } = useTheme()
  const bridge = window.reachout

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
    <CommandDialog open={open} onOpenChange={setOpen} title="Command palette" description="Jump to a screen or run an action">
      <Command>
      <CommandInput placeholder="Type a command…" />
      <CommandList>
        <CommandEmpty>No matching commands.</CommandEmpty>
        <CommandGroup heading="Go to">
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
