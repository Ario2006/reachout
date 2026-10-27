import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from 'recharts'
import type { Metrics } from '@/lib/api/types'

type Funnel = Metrics['funnel']
type PerDay = Metrics['per_day']

const STAGE_LABEL: Record<Funnel[number]['stage'], string> = {
  leads: 'Leads', drafted: 'Drafted', approved: 'Approved', sent: 'Sent', delivered: 'Delivered', replied: 'Replied',
}

const BAR = 'var(--color-primary)'
const TICK = { fill: 'var(--color-muted-foreground)', fontSize: 11 }
// jsdom has no layout, so the container needs a size to render anything; real windows resize it immediately.
const INITIAL = { width: 520, height: 200 }

/** One hue, one bar per stage, count as a label: the length carries the magnitude, so no legend is needed. The visual
 *  chart is hidden from assistive tech; an equivalent list is exposed instead. */
export function FunnelChart({ funnel }: { funnel: Funnel }) {
  const data = funnel.map((f) => ({ label: STAGE_LABEL[f.stage], count: f.count }))
  return (
    <figure aria-label="Funnel from leads to replies">
      <div aria-hidden>
        <ResponsiveContainer width="100%" height={data.length * 34 + 8} initialDimension={{ ...INITIAL, height: data.length * 34 + 8 }}>
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 0 }} barCategoryGap={8}>
            <XAxis type="number" hide domain={[0, (max: number) => Math.max(1, max)]} />
            <YAxis type="category" dataKey="label" width={80} tickLine={false} axisLine={false} tick={TICK} />
            <Bar dataKey="count" fill={BAR} radius={[0, 4, 4, 0]} isAnimationActive={false}>
              <LabelList dataKey="count" position="right" className="fill-foreground text-xs font-medium" formatter={(v: unknown) => Number(v).toLocaleString()} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ul className="sr-only" data-testid="funnel">
        {data.map((d) => <li key={d.label}>{d.label} {d.count.toLocaleString()}</li>)}
      </ul>
    </figure>
  )
}

function shortDay(day: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function DayTooltip({ active, payload }: TooltipContentProps) {
  const d = active ? (payload?.[0]?.payload as PerDay[number] | undefined) : undefined
  if (!d) return null
  return (
    <div className="bg-popover text-popover-foreground rounded-md border px-2.5 py-1.5 text-xs shadow-sm">
      <div className="font-medium">{shortDay(d.day)}</div>
      <div>{d.sent} sent, {d.replies} replies, {d.bounces} bounces</div>
    </div>
  )
}

/** Emails sent per local day. Single series: the card title names it, so there is no legend. A table is exposed to
 *  screen readers; the hover tooltip is visual only. */
export function SendsChart({ days }: { days: PerDay }) {
  const total = days.reduce((s, d) => s + d.sent, 0)
  return (
    <figure aria-label="Emails sent per day">
      <div className="text-muted-foreground mb-1 h-5 text-xs" data-testid="sends-readout">{total.toLocaleString()} emails in this period</div>
      <div aria-hidden>
        <ResponsiveContainer width="100%" height={180} initialDimension={INITIAL}>
          <BarChart data={days} margin={{ top: 8, right: 4, bottom: 0, left: -20 }}>
            <CartesianGrid vertical={false} stroke="var(--color-border)" />
            <XAxis dataKey="day" tickFormatter={shortDay} tick={TICK} tickLine={false} axisLine={false} minTickGap={24} />
            <YAxis allowDecimals={false} tick={TICK} tickLine={false} axisLine={false} />
            <Tooltip content={DayTooltip} cursor={{ fill: 'var(--color-muted)', opacity: 0.5 }} isAnimationActive={false} />
            <Bar dataKey="sent" fill={BAR} radius={[3, 3, 0, 0]} maxBarSize={24} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <table className="sr-only">
        <caption>Emails sent per day</caption>
        <thead><tr><th scope="col">Day</th><th scope="col">Sent</th><th scope="col">Replies</th><th scope="col">Bounces</th></tr></thead>
        <tbody>{days.map((d) => <tr key={d.day}><td>{d.day}</td><td>{d.sent}</td><td>{d.replies}</td><td>{d.bounces}</td></tr>)}</tbody>
      </table>
    </figure>
  )
}
