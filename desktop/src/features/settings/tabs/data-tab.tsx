import { useQuery } from '@tanstack/react-query'
import { FolderOpen, ScrollText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Section } from '../form-bits'

export function DataTab() {
  const info = useQuery({
    queryKey: ['app-info'],
    queryFn: () => (window.reachout ? window.reachout.getAppInfo() : Promise.resolve(null)),
  })
  const bridge = window.reachout
  return (
    <Section title="Your data" description="Everything is stored locally on this Mac. Nothing is uploaded anywhere.">
      <dl className="space-y-1 text-sm">
        <dt className="text-muted-foreground">Data folder</dt>
        <dd className="font-mono text-xs break-all">{info.data?.dataDir ?? '~/Reachout'}</dd>
        <dt className="text-muted-foreground mt-3">Contains</dt>
        <dd>reachout.db (leads, drafts, settings), exports/, logs/</dd>
      </dl>
      <div className="flex gap-2">
        <Button variant="outline" disabled={!bridge} onClick={() => void bridge?.openDataFolder()}>
          <FolderOpen aria-hidden /> Open data folder
        </Button>
        <Button variant="outline" disabled={!bridge} onClick={() => void bridge?.openLogs()}>
          <ScrollText aria-hidden /> Show logs
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">
        Export-everything and delete-everything arrive in a later milestone.
      </p>
    </Section>
  )
}
