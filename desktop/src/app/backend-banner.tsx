import { Loader2, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { BackendStatus } from '@shared/bridge'
import { Button } from '@/components/ui/button'
import { useLive } from '@/lib/jobs-store'

/** Thin banner when the backend is restarting or the live event stream is down. */
export function BackendBanner() {
  const [status, setStatus] = useState<BackendStatus | null>(null)
  const stream = useLive((s) => s.stream)
  const bridge = window.reachout

  useEffect(() => {
    void bridge?.getBackendStatus().then(setStatus)
    return bridge?.onBackendStatus(setStatus)
  }, [bridge])

  const restarting = status !== null && status.state !== 'ready'
  if (!restarting && stream !== 'reconnecting') return null

  return (
    <div role="status" aria-live="polite" className="bg-muted text-muted-foreground flex items-center gap-2 border-b px-4 py-1.5 text-xs">
      {restarting ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <TriangleAlert className="size-3.5" aria-hidden />}
      <span className="flex-1">
        {restarting ? status.message : 'Live updates are reconnecting…'}
      </span>
      {bridge ? <Button size="xs" variant="ghost" onClick={() => void bridge.openLogs()}>View logs</Button> : null}
    </div>
  )
}
