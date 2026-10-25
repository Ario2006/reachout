import { useQuery } from '@tanstack/react-query'
import type { ThemePreference } from '@shared/bridge'
import { Monitor, Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useHealth } from '@/lib/api/hooks'
import { useTheme } from '@/lib/theme'
import { Section } from '../form-bits'

const THEMES: { value: ThemePreference; label: string; Icon: typeof Sun }[] = [
  { value: 'system', label: 'System', Icon: Monitor },
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'dark', label: 'Dark', Icon: Moon },
]

export function AboutTab() {
  const health = useHealth()
  const info = useQuery({
    queryKey: ['app-info'],
    queryFn: () => (window.reachout ? window.reachout.getAppInfo() : Promise.resolve(null)),
  })
  const { preference, setPreference } = useTheme()

  return (
    <div className="space-y-8">
      <Section title="Appearance">
        <div role="group" aria-label="Theme" className="flex gap-2">
          {THEMES.map(({ value, label, Icon }) => (
            <Button key={value} variant={preference === value ? 'default' : 'outline'} aria-pressed={preference === value} onClick={() => setPreference(value)}>
              <Icon aria-hidden /> {label}
            </Button>
          ))}
        </div>
      </Section>

      <Section title="About Reachout">
        <dl className="grid grid-cols-[10rem_1fr] gap-y-1 text-sm">
          <dt className="text-muted-foreground">Version</dt><dd>{health.data?.version ?? '…'}</dd>
          <dt className="text-muted-foreground">Database schema</dt><dd>{health.data?.schema_version ?? '…'}</dd>
          <dt className="text-muted-foreground">Worker</dt>
          <dd>{health.data ? (health.data.worker.alive ? `running (pid ${health.data.worker.pid})` : 'not running') : '…'}</dd>
          <dt className="text-muted-foreground">Electron</dt><dd>{info.data?.electron ?? 'n/a'}</dd>
        </dl>
        <p className="text-muted-foreground text-sm">
          Open and click tracking are intentionally not supported: they are unreliable and need a
          public server.
        </p>
      </Section>

      <Section title="Keyboard shortcuts">
        <dl className="grid grid-cols-[10rem_1fr] gap-y-1 text-sm">
          <dt><kbd className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">⌘K</kbd></dt><dd>Command palette</dd>
          <dt><kbd className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">⌘,</kbd></dt><dd>Settings</dd>
        </dl>
      </Section>
    </div>
  )
}
