import { create } from 'zustand'
import { z } from 'zod'
import type { StreamState } from './sse'

export const jobEventSchema = z.object({
  id: z.number(),
  kind: z.string(),
  state: z.enum(['queued', 'running', 'succeeded', 'failed', 'cancelled']),
  progress: z.number(),
  total: z.number(),
  message: z.string().nullable(),
  error: z.string().nullable(),
})
export type JobEvent = z.infer<typeof jobEventSchema>

/** Normalise a REST job (loose optional fields) into the strict event shape, or null. */
export function toJobEvent(j: { id: number; kind: string; state: string; progress: number; total: number; message?: string | null; error?: string | null }): JobEvent | null {
  const r = jobEventSchema.safeParse({ ...j, message: j.message ?? null, error: j.error ?? null })
  return r.success ? r.data : null
}

export const TERMINAL: ReadonlySet<JobEvent['state']> = new Set(['succeeded', 'failed', 'cancelled'])

interface LiveState {
  jobs: Record<number, JobEvent>
  stream: StreamState
  upsertJob: (j: JobEvent) => void
  setStream: (s: StreamState) => void
}

/** Live job state fed by the SSE stream. Queries seed it; events keep it fresh. */
export const useLive = create<LiveState>((set) => ({
  jobs: {},
  stream: 'connecting',
  upsertJob: (j) => set((s) => ({ jobs: { ...s.jobs, [j.id]: j } })),
  setStream: (stream) => set({ stream }),
}))
