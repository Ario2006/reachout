import { useEffect, useRef, useState } from 'react'
import { Input } from '@/components/ui/input'

interface Props {
  value: string
  label: string
  onSave: (value: string) => Promise<void> | void
  className?: string
}

/** Text that turns into an input on double-click or Enter. Enter saves, Escape cancels, blur saves. */
export function EditableCell({ value, label, onSave, className }: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const input = useRef<HTMLInputElement>(null)
  const cancelled = useRef(false)

  useEffect(() => { if (!editing) setDraft(value) }, [value, editing])
  useEffect(() => { if (editing) input.current?.select() }, [editing])

  async function commit() {
    if (cancelled.current) { cancelled.current = false; return }
    setEditing(false)
    const next = draft.trim()
    if (next !== value) await onSave(next)
  }

  if (editing) {
    return (
      <Input
        ref={input}
        aria-label={`Edit ${label}`}
        value={draft}
        className="h-7 text-sm"
        onChange={(e) => setDraft(e.target.value)}
        onClick={(e) => e.stopPropagation()}
        onBlur={() => void commit()}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter') void commit()
          if (e.key === 'Escape') { cancelled.current = true; setDraft(value); setEditing(false) }
        }}
      />
    )
  }
  return (
    <span
      role="button"
      tabIndex={0}
      aria-label={`${label}: ${value || 'empty'}. Double-click or press Enter to edit`}
      title="Double-click to edit"
      className={className}
      onDoubleClick={(e) => { e.stopPropagation(); setEditing(true) }}
      onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setEditing(true) } }}
    >
      {value || <span className="text-muted-foreground/60">—</span>}
    </span>
  )
}
