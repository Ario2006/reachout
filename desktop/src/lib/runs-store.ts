import { create } from 'zustand'
import { z } from 'zod'

export const runEventSchema = z.object({
  run_id: z.number(),
  state: z.string(),
  stage: z.string().nullable().optional(),
  stats: z.record(z.string(), z.unknown()).default({}),
})
export const domainEventSchema = z.object({
  run_id: z.number(),
  domain: z.string(),
  state: z.string(),
  pages: z.number().default(0),
  emails: z.number().default(0),
  leads: z.number().default(0),
  note: z.string().nullable().optional(),
})
export const logEventSchema = z.object({ run_id: z.number(), message: z.string() })

export interface LiveDomain {
  domain: string
  state: string
  pages: number
  emails: number
  leads: number
  note: string
}

export interface LiveRun {
  state: string
  stage: string
  stats: Record<string, unknown>
  domains: Record<string, LiveDomain>
  order: string[]
  log: string[]
}

const MAX_LOG = 400
const empty = (): LiveRun => ({ state: 'queued', stage: 'searching', stats: {}, domains: {}, order: [], log: [] })

interface RunsState {
  runs: Record<number, LiveRun>
  /** Bumped on every `lead` event so the leads table can refresh without polling. */
  leadTick: number
  seed: (id: number, v: { state: string; stage?: string | null; stats: Record<string, unknown>; domains: LiveDomain[]; log: string[] }) => void
  applyRun: (e: z.infer<typeof runEventSchema>) => void
  applyDomain: (e: z.infer<typeof domainEventSchema>) => void
  applyLog: (e: z.infer<typeof logEventSchema>) => void
  bumpLeads: () => void
}

export const useLiveRuns = create<RunsState>((set) => ({
  runs: {},
  leadTick: 0,
  seed: (id, v) =>
    set((s) => {
      const cur = s.runs[id] ?? empty()
      const domains = { ...Object.fromEntries(v.domains.map((d) => [d.domain, d])), ...cur.domains }
      const order = [...new Set([...v.domains.map((d) => d.domain), ...cur.order])]
      const log = cur.log.length > 0 ? cur.log : v.log
      return { runs: { ...s.runs, [id]: { ...cur, state: v.state, stage: v.stage ?? cur.stage, stats: { ...v.stats, ...cur.stats }, domains, order, log } } }
    }),
  applyRun: (e) =>
    set((s) => {
      const cur = s.runs[e.run_id] ?? empty()
      return { runs: { ...s.runs, [e.run_id]: { ...cur, state: e.state, stage: e.stage ?? cur.stage, stats: e.stats } } }
    }),
  applyDomain: (e) =>
    set((s) => {
      const cur = s.runs[e.run_id] ?? empty()
      const d: LiveDomain = { domain: e.domain, state: e.state, pages: e.pages, emails: e.emails, leads: e.leads, note: e.note ?? '' }
      const order = cur.domains[e.domain] ? cur.order : [...cur.order, e.domain]
      return { runs: { ...s.runs, [e.run_id]: { ...cur, domains: { ...cur.domains, [e.domain]: d }, order } } }
    }),
  applyLog: (e) =>
    set((s) => {
      const cur = s.runs[e.run_id] ?? empty()
      return { runs: { ...s.runs, [e.run_id]: { ...cur, log: [...cur.log, e.message].slice(-MAX_LOG) } } }
    }),
  bumpLeads: () => set((s) => ({ leadTick: s.leadTick + 1 })),
}))

export const num = (stats: Record<string, unknown>, key: string): number => {
  const v = stats[key]
  return typeof v === 'number' ? v : 0
}
