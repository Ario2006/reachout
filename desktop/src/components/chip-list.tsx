import { X } from 'lucide-react'
import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'

interface Props {
  label: string
  items: string[]
  onChange: (items: string[]) => void
  placeholder?: string
  max?: number
  disabled?: boolean
}

/** Editable list of removable chips. Enter or comma adds; each chip has a labelled remove button. */
export function ChipList({ label, items, onChange, placeholder, max = 12, disabled }: Props) {
  const [draft, setDraft] = useState('')
  const id = `chips-${label.toLowerCase().replace(/\W+/g, '-')}`

  function commit() {
    const v = draft.trim().replace(/,$/, '')
    if (v && items.length < max && !items.some((i) => i.toLowerCase() === v.toLowerCase())) onChange([...items, v])
    setDraft('')
  }

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label} <span className="text-muted-foreground font-normal">({items.length}/{max})</span>
      </label>
      <div className="flex flex-wrap gap-1.5" role="list" aria-label={label}>
        {items.map((item) => (
          <Badge key={item} variant="secondary" role="listitem" className="h-6 gap-1 pr-1 text-xs font-normal">
            <span className="max-w-72 truncate">{item}</span>
            <button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${item}`}
              className="hover:bg-foreground/10 focus-visible:ring-ring rounded-full p-0.5 outline-none focus-visible:ring-2"
              onClick={() => onChange(items.filter((i) => i !== item))}
            >
              <X className="size-3" aria-hidden />
            </button>
          </Badge>
        ))}
      </div>
      {items.length < max ? (
        <Input
          id={id}
          value={draft}
          disabled={disabled}
          placeholder={placeholder ?? 'Type and press Enter'}
          className="h-8 max-w-md"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault()
              commit()
            } else if (e.key === 'Backspace' && draft === '' && items.length > 0) {
              onChange(items.slice(0, -1))
            }
          }}
          onBlur={commit}
        />
      ) : null}
    </div>
  )
}
