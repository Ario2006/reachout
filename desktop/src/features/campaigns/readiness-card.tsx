import { AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import type { Readiness } from '@/lib/api/types'
import { cn } from '@/lib/utils'

export function ReadinessList({ readiness, loading, error, onRetry }: { readiness: Readiness | undefined; loading: boolean; error: string | null; onRetry: () => void }) {
  if (error !== null) {
    return (
      <div role="alert" className="text-destructive text-sm">
        Could not check readiness: {error}{' '}
        <button type="button" className="underline" onClick={onRetry}>Retry</button>
      </div>
    )
  }
  if (loading || readiness === undefined) {
    return <div className="space-y-2" aria-label="Checking readiness"><Skeleton className="h-5 w-2/3" /><Skeleton className="h-5 w-1/2" /><Skeleton className="h-5 w-3/5" /></div>
  }
  return (
    <ul className="divide-y" aria-label="Readiness checklist">
      {readiness.items.map((i) => {
        const Icon = i.ok ? CheckCircle2 : i.blocking ? XCircle : AlertTriangle
        const cls = i.ok ? 'text-emerald-600 dark:text-emerald-400' : i.blocking ? 'text-destructive' : 'text-amber-600 dark:text-amber-400'
        return (
          <li key={i.id} className="flex items-start gap-3 py-2" data-testid={`ready-${i.id}`} data-ok={i.ok}>
            <Icon className={cn('mt-0.5 size-4 shrink-0', cls)} role="img" aria-label={i.ok ? 'Done' : i.blocking ? 'Required' : 'Recommended'} />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{i.label}</div>
              <div className="text-muted-foreground text-sm break-words">{i.detail}</div>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
