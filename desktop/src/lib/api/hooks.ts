import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchText } from './client'
import type { EmailConfidence, GmailStatus, LeadPatch, LeadStatus, Mode, Plan, SettingsUpdate } from './types'

export const qk = {
  health: ['health'] as const,
  setupStatus: ['setup', 'status'] as const,
  models: ['setup', 'models'] as const,
  settings: ['settings'] as const,
  suppression: (q: string) => ['suppression', q] as const,
  runs: ['runs'] as const,
  run: (id: number) => ['run', id] as const,
  leads: (f: LeadQuery) => ['leads', f] as const,
  lead: (id: number) => ['lead', id] as const,
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


// ---- discovery ------------------------------------------------------------------------------

export const usePlan = () =>
  useMutation({
    mutationFn: (v: { request: string; mode?: Mode }) =>
      api.post('/discovery/plan', { body: v.mode ? { request: v.request, mode: v.mode } : { request: v.request } }),
  })

export function useStartRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: {
      request_text: string
      plan: Plan
      options: { recrawl: boolean; use_overpass: boolean; expand_lists: boolean; max_domains?: number }
    }) => api.post('/discovery/run', { body: v }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.runs }),
  })
}

export const useRuns = () =>
  useQuery({ queryKey: qk.runs, queryFn: () => api.get('/discovery/runs'), refetchInterval: 15_000 })

export const useRun = (id: number | null) =>
  useQuery({
    queryKey: qk.run(id ?? 0),
    queryFn: () => api.get('/discovery/runs/{run_id}', { params: { run_id: id ?? 0 } }),
    enabled: id !== null,
    // The event stream drives the live view; this is the safety net if it drops or an event is missed.
    refetchInterval: (q) => (q.state.data && (q.state.data.state === 'queued' || q.state.data.state === 'running') ? 4000 : false),
  })

export function useCancelRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.post('/discovery/runs/{run_id}/cancel', { params: { run_id: id } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.runs }),
  })
}

export function useResumeRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.post('/discovery/runs/{run_id}/resume', { params: { run_id: id } }),
    onSuccess: (_run, id) => {
      void qc.invalidateQueries({ queryKey: qk.runs })
      void qc.invalidateQueries({ queryKey: qk.run(id) })
    },
  })
}

// ---- leads ----------------------------------------------------------------------------------

export interface LeadQuery {
  q: string
  status: LeadStatus[]
  mode: Mode[]
  confidence: EmailConfidence[]
  run_id: number | null
  min_score: number | null
  sort: string
}

export const EMPTY_QUERY: LeadQuery = { q: '', status: [], mode: [], confidence: [], run_id: null, min_score: null, sort: '' }
export const LEADS_LIMIT = 5000

const leadParams = (f: LeadQuery) => ({
  q: f.q || undefined, status: f.status, mode: f.mode, confidence: f.confidence,
  run_id: f.run_id ?? undefined, min_score: f.min_score ?? undefined, sort: f.sort || undefined,
})

export const useLeads = (f: LeadQuery) =>
  useQuery({
    queryKey: qk.leads(f),
    queryFn: () => api.get('/leads', { query: { ...leadParams(f), limit: LEADS_LIMIT } }),
    placeholderData: (prev) => prev, // keep rows on screen while a new filter loads
  })

export const useLead = (id: number | null) =>
  useQuery({
    queryKey: qk.lead(id ?? 0),
    queryFn: () => api.get('/leads/{lead_id}', { params: { lead_id: id ?? 0 } }),
    enabled: id !== null,
  })

export function usePatchLead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: number; patch: LeadPatch }) =>
      api.patch('/leads/{lead_id}', { params: { lead_id: v.id }, body: v.patch }),
    onSuccess: (lead) => {
      qc.setQueryData(qk.lead(lead.id), lead)
      void qc.invalidateQueries({ queryKey: ['leads'] })
    },
  })
}

export type BulkFilter = Omit<LeadQuery, 'sort'>

/** Either explicit ids, or "everything matching this filter". */
export type BulkBody =
  | { action: 'delete' | 'suppress'; ids: number[] }
  | { action: 'delete' | 'suppress'; filter: BulkFilter }
  | { action: 'set_status'; status: LeadStatus; ids: number[] }
  | { action: 'set_status'; status: LeadStatus; filter: BulkFilter }

export function useBulk() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BulkBody) => api.post('/leads/bulk', { body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['leads'] })
      void qc.invalidateQueries({ queryKey: ['suppression'] })
    },
  })
}

export function useImportCsv() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { text: string; dryRun: boolean }) =>
      api.post('/leads/import-csv', { query: { dry_run: v.dryRun }, raw: { text: v.text, contentType: 'text/csv' } }),
    onSuccess: (_r, v) => {
      if (!v.dryRun) void qc.invalidateQueries({ queryKey: ['leads'] })
    },
  })
}

/** Fetch the CSV for the current filters and hand it to the browser as a download. */
export async function downloadLeadsCsv(f: LeadQuery, filename = 'leads.csv'): Promise<void> {
  const text = await fetchText('/leads/export.csv', leadParams(f))
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
