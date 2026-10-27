import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import { K } from './keys'
import type { CampaignIn, JobOut, MessageOut } from './types'

export type MessageState = MessageOut['state']

export const useCampaigns = () => useQuery({ queryKey: K.campaigns, queryFn: () => api.get('/campaigns') })

export const useCampaign = (id: number | null) =>
  useQuery({
    queryKey: [...K.campaigns, id ?? 0, 'detail'],
    queryFn: () => api.get('/campaigns/{campaign_id}', { params: { campaign_id: id ?? 0 } }),
    enabled: id !== null,
  })

export const useReadiness = (id: number | null) =>
  useQuery({
    queryKey: [...K.campaigns, id ?? 0, 'readiness'],
    queryFn: () => api.get('/campaigns/{campaign_id}/readiness', { params: { campaign_id: id ?? 0 } }),
    enabled: id !== null,
  })

export const useCampaignStats = (id: number | null) =>
  useQuery({
    queryKey: [...K.campaigns, id ?? 0, 'stats'],
    queryFn: () => api.get('/campaigns/{campaign_id}/stats', { params: { campaign_id: id ?? 0 } }),
    enabled: id !== null,
  })

export const useCampaignMessages = (id: number | null, state?: MessageState) =>
  useQuery({
    queryKey: [...K.campaigns, id ?? 0, 'messages', state ?? 'all'],
    queryFn: () =>
      api.get('/campaigns/{campaign_id}/messages', { params: { campaign_id: id ?? 0 }, query: state ? { state } : {} }),
    enabled: id !== null,
  })

export const useSendingStatus = () =>
  useQuery({ queryKey: [...K.sending, 'status'], queryFn: () => api.get('/sending/status'), refetchInterval: 15_000 })

export function useCreateCampaign() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CampaignIn) => api.post('/campaigns', { body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: K.campaigns }),
  })
}

export function useUpdateCampaign(id: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CampaignIn) => api.put('/campaigns/{campaign_id}', { params: { campaign_id: id }, body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: K.campaigns }),
  })
}

export type CampaignAction = 'start' | 'pause' | 'resume'

export function useCampaignAction(id: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (action: CampaignAction) => {
      const params = { campaign_id: id }
      if (action === 'start') return api.post('/campaigns/{campaign_id}/start', { params })
      if (action === 'pause') return api.post('/campaigns/{campaign_id}/pause', { params })
      return api.post('/campaigns/{campaign_id}/resume', { params })
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: K.campaigns })
      void qc.invalidateQueries({ queryKey: K.sending })
    },
  })
}

export function useTestSend(id: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/campaigns/{campaign_id}/test-send', { params: { campaign_id: id } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: K.campaigns }),
  })
}

export function useDryRun(id: number) {
  return useMutation({ mutationFn: () => api.post('/campaigns/{campaign_id}/dry-run', { params: { campaign_id: id } }) })
}

/** Poll a job until it finishes (the SSE stream also updates it, but a poll keeps the dialog honest after a reconnect). */
export const useJob = (jobId: number | null) =>
  useQuery<JobOut>({
    queryKey: ['jobs', jobId ?? 0],
    queryFn: () => api.get('/jobs/{job_id}', { params: { job_id: jobId ?? 0 } }),
    enabled: jobId !== null,
    refetchInterval: (q) => {
      const s = q.state.data?.state
      return s === 'succeeded' || s === 'failed' || s === 'cancelled' ? false : 800
    },
  })

export function useConfirmMessage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: number; wasSent: boolean }) =>
      api.post('/messages/{message_id}/confirm', { params: { message_id: v.id }, body: { was_sent: v.wasSent } }),
    onSettled: () => void qc.invalidateQueries({ queryKey: K.campaigns }),
  })
}

export function useRetryMessage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.post('/messages/{message_id}/retry', { params: { message_id: id } }),
    onSettled: () => void qc.invalidateQueries({ queryKey: K.campaigns }),
  })
}

export function useSetPauseAll() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (paused: boolean) => (paused ? api.post('/sending/pause-all') : api.post('/sending/resume-all')),
    onSuccess: (data) => {
      qc.setQueryData([...K.sending, 'status'], data)
      void qc.invalidateQueries({ queryKey: K.campaigns })
    },
  })
}
