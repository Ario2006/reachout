import { AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'
import type { Check } from '@/lib/api/types'
import { cn } from '@/lib/utils'

const ICON = {
  ok: { Icon: CheckCircle2, cls: 'text-emerald-600 dark:text-emerald-400', label: 'Passed' },
  warn: { Icon: AlertTriangle, cls: 'text-amber-600 dark:text-amber-400', label: 'Warning' },
  fail: { Icon: XCircle, cls: 'text-destructive', label: 'Failed' },
} as const

export function CheckRow({ check }: { check: Check }) {
  const kind = check.status === 'ok' || check.status === 'warn' ? check.status : 'fail'
  const { Icon, cls, label } = ICON[kind]
  return (
    <div className="flex items-start gap-3 py-2" data-testid={`check-${check.id}`}>
      <Icon className={cn('mt-0.5 size-4 shrink-0', cls)} aria-label={label} role="img" />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{check.name}</div>
        <div className="text-muted-foreground text-sm break-words">{check.detail}</div>
        {kind !== 'ok' && check.hint ? (
          <div className="text-muted-foreground mt-0.5 text-xs">{check.hint}</div>
        ) : null}
      </div>
    </div>
  )
}
