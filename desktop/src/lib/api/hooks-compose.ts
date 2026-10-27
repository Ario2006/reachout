/** Query hooks for Compose: templates, profile fields, drafts. Keys come from ./keys (SSE invalidates by prefix). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import { K } from './keys'
import type { components } from './schema'
import type { Campaign, CampaignIn, DraftKind, DraftPatch, DraftsPage, DraftStatus, ProfileField, TemplateIn } from './types'

type S = components['schemas']
export type DraftItem = S['DraftOut']
export type BulkApproveResult = S['BulkApproveOut']

// ---- templates -----------------------------------------------------------------------------
export const useTemplates = () => useQuery({ queryKey: K.templates, queryFn: () => api.get('/templates') })

export function useSaveTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: TemplateIn }) =>
      id === null ? api.post('/templates', { body }) : api.put('/templates/{template_id}', { params: { template_id: id }, body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: K.templates }),
  })
}

export function useDeleteTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.delete('/templates/{template_id}', { params: { template_id: id } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: K.templates }),
  })
}

/** Live preview. Pass already-debounced input; the previous preview stays on screen while a new one loads. */
export const useTemplatePreview = (template: TemplateIn | null, leadId: number | null) =>
  useQuery({
    queryKey: [...K.templates, 'preview', template, leadId] as const,
    queryFn: () => api.post('/templates/preview', { body: { template: template as TemplateIn, lead_id: leadId } }),
    enabled: template !== null,
    placeholderData: (prev) => prev,
    retry: false,
  })

// ---- profile -------------------------------------------------------------------------------
export const useProfile = () => useQuery({ queryKey: K.profile, queryFn: () => api.get('/profile') })

export function usePutProfile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (fields: ProfileField[]) => api.put('/profile', { body: { fields } }),
    onSuccess: (data) => {
      qc.setQueryData(K.profile, data)
      void qc.invalidateQueries({ queryKey: K.templates }) // merge-field chips list the profile keys
    },
  })
}

// ---- campaigns (list and create are real endpoints owned by the Campaigns screen; same cache key) ------
export const useCampaignList = () => useQuery({ queryKey: K.campaigns, queryFn: () => api.get('/campaigns') })

export function useCreateCampaign() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CampaignIn) => api.post('/campaigns', { body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: K.campaigns }),
  })
}

/** Changing the template re-sends the campaign's other settings unchanged (PUT takes the whole record). */
export function useSetCampaignTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ campaign: c, templateId }: { campaign: Campaign; templateId: number }) => {
      const body: CampaignIn = {
        name: c.name, template_id: templateId, mode: c.mode, attachment_path: c.attachment_path, daily_cap: c.daily_cap,
        window_start: c.window_start, window_end: c.window_end, delay_min_s: c.delay_min_s, delay_max_s: c.delay_max_s,
        followups_enabled: c.followups_enabled, followup_days: c.followup_days, followups_require_approval: c.followups_require_approval,
      }
      return api.put('/campaigns/{campaign_id}', { params: { campaign_id: c.id }, body })
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: K.campaigns }),
  })
}

// ---- drafts --------------------------------------------------------------------------------
/** 'followups' covers follow-up 1 and 2 (the API filters by one kind at a time, so both are fetched and merged). */
export type DraftKindFilter = 'initial' | 'followups'

export interface DraftQuery {
  campaignId: number | null
  status: DraftStatus[]
  q: string
  kind?: DraftKindFilter
}
export const DRAFTS_LIMIT = 500

export const useDrafts = (f: DraftQuery) =>
  useQuery({
    queryKey: [...K.drafts, 'list', f] as const,
    queryFn: async (): Promise<DraftsPage> => {
      const fetchKind = (kind: DraftKind) =>
        api.get('/drafts', { query: { campaign_id: f.campaignId ?? 0, status: f.status, q: f.q || undefined, limit: DRAFTS_LIMIT, kind } })
      if ((f.kind ?? 'initial') === 'initial') return fetchKind('initial')
      const [one, two] = await Promise.all([fetchKind('followup_1'), fetchKind('followup_2')])
      const a = one.counts
      const b = two.counts
      const counts = { pending: a.pending + b.pending, needs_review: a.needs_review + b.needs_review, approved: a.approved + b.approved, rejected: a.rejected + b.rejected }
      return { ...one, items: [...one.items, ...two.items].sort((a, b) => a.id - b.id), total: one.total + two.total, counts }
    },
    enabled: f.campaignId !== null,
    placeholderData: (prev) => prev,
  })

export const useDraft = (id: number | null) =>
  useQuery({
    queryKey: [...K.drafts, 'detail', id] as const,
    queryFn: () => api.get('/drafts/{draft_id}', { params: { draft_id: id ?? 0 } }),
    enabled: id !== null,
  })

export function usePatchDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: DraftPatch }) =>
      api.patch('/drafts/{draft_id}', { params: { draft_id: id }, body: patch }),
    onSuccess: (data) => qc.setQueryData([...K.drafts, 'detail', data.id], data),
    onSettled: () => void qc.invalidateQueries({ queryKey: K.drafts }),
  })
}

export function useRegenerateOpener() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.post('/drafts/{draft_id}/regenerate-opener', { params: { draft_id: id } }),
    onSuccess: (data) => qc.setQueryData([...K.drafts, 'detail', data.id], data),
    onSettled: () => void qc.invalidateQueries({ queryKey: K.drafts }),
  })
}

export function useBulkApprove() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (campaignId: number) => api.post('/drafts/bulk-approve', { body: { campaign_id: campaignId, only_unflagged: true } }),
    onSettled: () => void qc.invalidateQueries({ queryKey: K.drafts }),
  })
}

export function useGenerateDrafts() {
  return useMutation({
    mutationFn: (body: { campaignId: number; leadIds?: number[]; regenerate?: boolean }) =>
      api.post('/drafts/generate', { body: { campaign_id: body.campaignId, lead_ids: body.leadIds ?? null, regenerate: body.regenerate ?? false } }),
  })
}

/** A short list of leads to preview a template against (not the whole table). */
export const usePreviewLeads = () =>
  useQuery({ queryKey: ['leads', 'preview-picker'] as const, queryFn: () => api.get('/leads', { query: { limit: 25 } }), staleTime: 30_000 })
