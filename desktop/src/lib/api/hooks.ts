import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type { GmailStatus, SettingsUpdate } from './types'

export const qk = {
  health: ['health'] as const,
  setupStatus: ['setup', 'status'] as const,
  models: ['setup', 'models'] as const,
  settings: ['settings'] as const,
  suppression: (q: string) => ['suppression', q] as const,
}

export const useHealth = () =>
  useQuery({ queryKey: qk.health, queryFn: () => api.get('/health'), refetchInterval: 10_000 })

export const useSetupStatus = (pollMs?: number) =>
  useQuery({
    queryKey: qk.setupStatus,
    queryFn: () => api.get('/setup/status'),
    refetchInterval: pollMs ?? 15_000,
  })

export const useInstalledModels = () =>
  useQuery({ queryKey: qk.models, queryFn: () => api.get('/setup/models') })

export const useSettings = () =>
  useQuery({ queryKey: qk.settings, queryFn: () => api.get('/settings') })

export function useUpdateSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SettingsUpdate) => api.put('/settings', { body }),
    onSuccess: (data) => {
      qc.setQueryData(qk.settings, data)
      void qc.invalidateQueries({ queryKey: qk.setupStatus })
    },
  })
}

export function useSaveGmail() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { address: string; app_password?: string | null }) =>
      api.put('/settings/gmail', { body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.settings })
      void qc.invalidateQueries({ queryKey: qk.setupStatus })
    },
  })
}

export function useRemoveGmailPassword() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (): Promise<GmailStatus> => api.delete('/settings/gmail/password'),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.settings })
      void qc.invalidateQueries({ queryKey: qk.setupStatus })
    },
  })
}

export const useTestGmail = () => useMutation({ mutationFn: () => api.post('/setup/test-gmail') })

export function usePullModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (model?: string) => api.post('/setup/pull-model', { body: model ? { model } : {} }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.setupStatus }),
  })
}

export const useCancelJob = () =>
  useMutation({
    mutationFn: (jobId: number) => api.post('/jobs/{job_id}/cancel', { params: { job_id: jobId } }),
  })

export function useCompleteSetup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { display_name: string; footer_text: string; legal_acknowledged: boolean }) =>
      api.post('/setup/complete', { body }),
    onSuccess: (data) => {
      qc.setQueryData(qk.setupStatus, data)
      void qc.invalidateQueries({ queryKey: qk.settings })
    },
  })
}

export const useSuppression = (q: string) =>
  useQuery({
    queryKey: qk.suppression(q),
    queryFn: () => api.get('/suppression', { query: { q } }),
  })

export function useAddSuppression() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (email: string) => api.post('/suppression', { body: { email } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['suppression'] }),
  })
}

export function useRemoveSuppression() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { email: string; confirm: string }) => api.delete('/suppression', { query: v }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['suppression'] }),
  })
}
