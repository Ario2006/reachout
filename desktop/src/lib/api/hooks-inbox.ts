/** Query hooks for the Inbox and Dashboard. Keys come from ./keys so the SSE router can invalidate by prefix. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import { K } from './keys'
import type { LeadStatus } from './types'

export type InboxFilter = 'all' | 'unread' | 'replied' | 'bounced' | 'opt_out' | 'auto_reply' | 'sent'
export type MetricsRange = '7d' | '30d' | 'all'
export type Outcome = 'interested' | 'not_interested' | 'meeting_booked'

export interface StatusChange {
  status?: LeadStatus
  outcome?: Outcome
  notes?: string
}

export const INBOX_FILTERS: { value: InboxFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'unread', label: 'Unread' },
  { value: 'replied', label: 'Replied' },
  { value: 'opt_out', label: 'Opt-outs' },
  { value: 'bounced', label: 'Bounced' },
  { value: 'auto_reply', label: 'Auto-replies' },
  { value: 'sent', label: 'No answer' },
]

export function useThreads(filter: InboxFilter, q: string) {
  return useQuery({
    queryKey: [...K.threads, 'list', { filter, q }],
    queryFn: () => api.get('/threads', { query: { filter, q, limit: 200 } }),
  })
}

export function useThread(leadId: number | null) {
  return useQuery({
    queryKey: [...K.threads, 'detail', leadId],
    queryFn: () => api.get('/threads/{lead_id}', { params: { lead_id: leadId ?? 0 } }),
    enabled: leadId !== null,
  })
}

/** Polls while a check runs so "Checking..." clears without waiting for the next SSE event. */
export function useInboxStatus() {
  return useQuery({
    queryKey: K.inboxStatus,
    queryFn: () => api.get('/inbox/status'),
    refetchInterval: (q) => (q.state.data?.checking ? 2_000 : 30_000),
  })
}

export function useCheckNow() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/inbox/check-now'),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: K.inboxStatus })
      void qc.invalidateQueries({ queryKey: K.threads })
    },
  })
}

export function useSetLeadStatus() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ leadId, change }: { leadId: number; change: StatusChange }) =>
      api.post('/leads/{lead_id}/status', { params: { lead_id: leadId }, body: change }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: K.threads })
      void qc.invalidateQueries({ queryKey: K.metrics })
      void qc.invalidateQueries({ queryKey: ['leads'] })
    },
  })
}

export function useConfirmOptOut() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (eventId: number) => api.post('/events/{event_id}/confirm-opt-out', { params: { event_id: eventId } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: K.threads })
      void qc.invalidateQueries({ queryKey: K.metrics })
    },
  })
}

export function useMetrics(range: MetricsRange, campaignId?: number) {
  return useQuery({
    queryKey: [...K.metrics, { range, campaignId }],
    queryFn: () => api.get('/metrics', { query: { range, campaign_id: campaignId } }),
  })
}

export function useActivity(limit = 30) {
  return useQuery({
    queryKey: [...K.activity, limit],
    queryFn: () => api.get('/activity', { query: { limit } }),
  })
}
