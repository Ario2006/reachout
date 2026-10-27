import { ChevronDown } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

interface Props<T extends string> {
  label: string
  options: { value: T; label: string }[]
  selected: T[]
  onChange: (next: T[]) => void
}

/** A filter chip with a checkbox menu. Shows how many values are active. */
export function FilterMenu<T extends string>({ label, options, selected, onChange }: Props<T>) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant={selected.length ? 'secondary' : 'outline'} size="sm" aria-label={`Filter by ${label.toLowerCase()}`}>
          {label}
          {selected.length ? <Badge className="h-4 min-w-4 px-1 text-[10px]">{selected.length}</Badge> : null}
          <ChevronDown aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-44">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {options.map((o) => (
          <DropdownMenuCheckboxItem
            key={o.value}
            checked={selected.includes(o.value)}
            onSelect={(e) => e.preventDefault()}
            onCheckedChange={(on) => onChange(on ? [...selected, o.value] : selected.filter((v) => v !== o.value))}
          >
            {o.label}
          </DropdownMenuCheckboxItem>
        ))}
        {selected.length ? (
          <>
            <DropdownMenuSeparator />
            <Button variant="ghost" size="xs" className="w-full" onClick={() => onChange([])}>Clear</Button>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
