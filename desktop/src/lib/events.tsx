import { useQueryClient } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { getConnection } from './backend'
import { jobEventSchema, TERMINAL, useLive } from './jobs-store'
import { qk } from './api/hooks'
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
            if (m.event !== 'job') return
            let payload: unknown
            try {
              payload = (JSON.parse(m.data) as { payload?: unknown }).payload
            } catch {
              return
            }
            const parsed = jobEventSchema.safeParse(payload)
            if (!parsed.success) return
            upsertJob(parsed.data)
            if (TERMINAL.has(parsed.data.state)) {
              void qc.invalidateQueries({ queryKey: qk.setupStatus })
              void qc.invalidateQueries({ queryKey: qk.models })
            }
          },
        }),
      )
      .catch(() => setStream('reconnecting'))

    return () => ac.abort()
  }, [qc])

  return <>{children}</>
}
