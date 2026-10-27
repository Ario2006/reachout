import { useQueryClient } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { getConnection } from './backend'
import { jobEventSchema, TERMINAL, useLive } from './jobs-store'
import { qk } from './api/hooks'
import { K } from './api/keys'
import { campaignEventSchema, draftEventSchema, inboxEventSchema, messageEventSchema, sendingEventSchema } from './event-types'
import { domainEventSchema, logEventSchema, runEventSchema, useLiveRuns } from './runs-store'
import { streamEvents } from './sse'

/** Opens /events once for the app's lifetime and routes events into the stores and query cache. */
export function EventsProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()

  useEffect(() => {
    const ac = new AbortController()
    const { upsertJob, setStream } = useLive.getState()

    void getConnection()
      .then((conn) =>
        streamEvents({
          ...conn,
          signal: ac.signal,
          onState: setStream,
          onMessage: (m) => {
            let payload: unknown
            try {
              payload = (JSON.parse(m.data) as { payload?: unknown }).payload
            } catch {
              return
            }
            const runs = useLiveRuns.getState()
            switch (m.event) {
              case 'job': {
                const parsed = jobEventSchema.safeParse(payload)
                if (!parsed.success) return
                upsertJob(parsed.data)
                if (TERMINAL.has(parsed.data.state)) {
                  void qc.invalidateQueries({ queryKey: qk.setupStatus })
                  void qc.invalidateQueries({ queryKey: qk.models })
                  if (parsed.data.kind === 'discovery_run') void qc.invalidateQueries({ queryKey: qk.runs })
                }
                return
              }
              case 'run': {
                const r = runEventSchema.safeParse(payload)
                if (r.success) {
                  runs.applyRun(r.data)
                  if (r.data.state !== 'running' && r.data.state !== 'queued') void qc.invalidateQueries({ queryKey: qk.runs })
                }
                return
              }
              case 'run_domain': {
                const d = domainEventSchema.safeParse(payload)
                if (d.success) runs.applyDomain(d.data)
                return
              }
              case 'run_log': {
                const l = logEventSchema.safeParse(payload)
                if (l.success) runs.applyLog(l.data)
                return
              }
              // Milestone 3-6 events carry hints only: invalidate by key prefix and let each screen refetch.
              case 'draft':
                if (draftEventSchema.safeParse(payload).success) void qc.invalidateQueries({ queryKey: K.drafts })
                return
              case 'campaign':
                if (campaignEventSchema.safeParse(payload).success) {
                  void qc.invalidateQueries({ queryKey: K.campaigns })
                  void qc.invalidateQueries({ queryKey: K.sending })
                  void qc.invalidateQueries({ queryKey: K.metrics })
                }
                return
              case 'message':
                if (messageEventSchema.safeParse(payload).success) {
                  void qc.invalidateQueries({ queryKey: K.campaigns })
                  void qc.invalidateQueries({ queryKey: K.threads })
                  void qc.invalidateQueries({ queryKey: K.followups })
                  void qc.invalidateQueries({ queryKey: K.metrics })
                  void qc.invalidateQueries({ queryKey: K.activity })
                }
                return
              case 'inbox':
                if (inboxEventSchema.safeParse(payload).success) {
                  void qc.invalidateQueries({ queryKey: K.threads })
                  void qc.invalidateQueries({ queryKey: K.inboxStatus })
                  void qc.invalidateQueries({ queryKey: K.metrics })
                  void qc.invalidateQueries({ queryKey: K.activity })
                  runs.bumpLeads()
                }
                return
              case 'sending':
                if (sendingEventSchema.safeParse(payload).success) void qc.invalidateQueries({ queryKey: K.sending })
                return
              case 'lead':
              case 'lead.updated':
                runs.bumpLeads()
                return
              default:
                return
            }
          },
        }),
      )
      .catch(() => setStream('reconnecting'))

    return () => ac.abort()
  }, [qc])

  return <>{children}</>
}
