import { AlertTriangle, Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'

export function Warnings({ items }: { items: string[] }) {
  if (items.length === 0) return null
  return (
    <Alert>
      <AlertTriangle aria-hidden />
      <AlertDescription>
        <ul className="list-disc space-y-1 pl-4">
          {items.map((w) => <li key={w}>{w}</li>)}
        </ul>
      </AlertDescription>
    </Alert>
  )
}

export function SaveBar({ saving, dirty, label = 'Save changes' }: { saving: boolean; dirty: boolean; label?: string }) {
  return (
    <div className="flex items-center gap-3 pt-2">
      <Button type="submit" disabled={saving || !dirty}>
        {saving ? <Loader2 className="animate-spin" aria-hidden /> : null} {label}
      </Button>
      {dirty ? <span className="text-muted-foreground text-xs">Unsaved changes</span> : null}
    </div>
  )
}

export function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-base font-medium">{title}</h2>
        {description ? <p className="text-muted-foreground text-sm">{description}</p> : null}
      </div>
      {children}
    </section>
  )
}
