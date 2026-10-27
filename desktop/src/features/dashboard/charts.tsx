import { useState } from 'react'
import type { Metrics } from '@/lib/api/types'

type Funnel = Metrics['funnel']
type PerDay = Metrics['per_day']

const STAGE_LABEL: Record<Funnel[number]['stage'], string> = {
  leads: 'Leads', drafted: 'Drafted', approved: 'Approved', sent: 'Sent', delivered: 'Delivered', replied: 'Replied',
}

/** One hue, one bar per stage, count as text: the length carries the magnitude, so no legend is needed. */
export function FunnelChart({ funnel }: { funnel: Funnel }) {
  const max = Math.max(1, ...funnel.map((f) => f.count))
  return (
    <figure aria-label="Funnel from leads to replies">
      <ul className="space-y-2" data-testid="funnel">
        {funnel.map((f, i) => (
          <li key={f.stage} className="grid grid-cols-[5.5rem_1fr_3rem] items-center gap-3 text-sm">
            <span className="text-muted-foreground">{STAGE_LABEL[f.stage]}</span>
            <div className="bg-muted h-4 overflow-hidden rounded-r-sm" aria-hidden>
              <div className="bg-primary h-full rounded-r-[4px]" style={{ width: `${(f.count / max) * 100}%`, opacity: 1 - i * 0.08 }} />
            </div>
            <span className="text-right font-medium tabular-nums">{f.count.toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </figure>
  )
}

const W = 560
const H = 160
const PAD = { l: 28, r: 4, t: 8, b: 20 }

function niceMax(n: number): number {
  if (n <= 5) return 5
  const p = 10 ** Math.floor(Math.log10(n))
  return Math.ceil(n / p) * p
}

function shortDay(day: string): string {
  const d = new Date(`${day}T12:00:00`)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** Emails sent per local day. Single series: the title names it, so there is no legend. A table view is exposed to
 *  screen readers, and every bar has a hover readout. */
export function SendsChart({ days }: { days: PerDay }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = niceMax(Math.max(0, ...days.map((d) => d.sent)))
  const slot = (W - PAD.l - PAD.r) / Math.max(1, days.length)
  const bw = Math.min(24, Math.max(3, slot - 2))
  const y = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - v / max)
  const hovered = hover === null ? null : days[hover]
  const total = days.reduce((s, d) => s + d.sent, 0)
  return (
    <figure aria-label="Emails sent per day">
      <div className="text-muted-foreground mb-1 h-5 text-xs" aria-live="polite" data-testid="sends-readout">
        {hovered ? `${shortDay(hovered.day)}: ${hovered.sent} sent, ${hovered.replies} replies, ${hovered.bounces} bounces` : `${total.toLocaleString()} emails in this period`}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`Emails sent per day, ${total} in total`}>
        {[0, max / 2, max].map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} className="stroke-border" strokeWidth={1} />
            <text x={PAD.l - 6} y={y(t) + 3} textAnchor="end" className="fill-muted-foreground text-[10px]">{Math.round(t)}</text>
          </g>
        ))}
        {days.map((d, i) => {
          const x = PAD.l + i * slot + (slot - bw) / 2
          const h = Math.max(d.sent > 0 ? 2 : 0, y(0) - y(d.sent))
          return (
            <g key={d.day} data-testid={`bar-${i}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={PAD.l + i * slot} y={PAD.t} width={slot} height={H - PAD.t - PAD.b} fill="transparent" />
              <rect x={x} y={y(0) - h} width={bw} height={h} rx={3} className={hover === i ? 'fill-primary' : 'fill-primary/80'} />
            </g>
          )
        })}
        {[0, Math.floor((days.length - 1) / 2), days.length - 1].filter((i, k, a) => days.length > 0 && a.indexOf(i) === k).map((i) => (
          <text key={i} x={PAD.l + i * slot + slot / 2} y={H - 4} textAnchor={i === 0 ? 'start' : i === days.length - 1 ? 'end' : 'middle'} className="fill-muted-foreground text-[10px]">
            {shortDay(days[i]!.day)}
          </text>
        ))}
      </svg>
      <table className="sr-only">
        <caption>Emails sent per day</caption>
        <thead><tr><th>Day</th><th>Sent</th><th>Replies</th><th>Bounces</th></tr></thead>
        <tbody>{days.map((d) => <tr key={d.day}><td>{d.day}</td><td>{d.sent}</td><td>{d.replies}</td><td>{d.bounces}</td></tr>)}</tbody>
      </table>
    </figure>
  )
}
