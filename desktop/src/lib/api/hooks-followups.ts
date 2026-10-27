/** Follow-up plan and skip action (milestone 6). The SSE router invalidates K.followups on its own. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from './client'
import { K } from './keys'
import type { FollowupKind } from './types'

/** The endpoints answer 501 (or 404) until the follow-ups backend is installed. */
export const isFollowupsUnavailable = (e: unknown): boolean => e instanceof ApiError && (e.status === 501 || e.status === 404)

export const useFollowupPlan = (campaignId: number | null) =>
  useQuery({
    queryKey: [...K.followups, campaignId ?? 0, 'plan'] as const,
    queryFn: () => api.get('/campaigns/{campaign_id}/followups', { params: { campaign_id: campaignId ?? 0 } }),
    enabled: campaignId !== null,
    retry: false,
  })

export function useSkipFollowup(campaignId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { leadId: number; kind: FollowupKind }) =>
      api.post('/campaigns/{campaign_id}/followups/skip', { params: { campaign_id: campaignId }, body: { lead_id: v.leadId, kind: v.kind } }),
    onSettled: () => void qc.invalidateQueries({ queryKey: K.followups }),
  })
}
