import { z } from 'zod'

/** Mirrors backend/core/event_types.py. Payloads are invalidation hints; the UI refetches the data. */
export const draftEventSchema = z.object({
  campaign_id: z.number(),
  draft_id: z.number().nullable(),
  action: z.enum(['generated', 'updated', 'approved', 'rejected', 'bulk']),
})
export const campaignEventSchema = z.object({
  campaign_id: z.number(),
  state: z.enum(['draft', 'running', 'paused', 'done']),
  paused_by: z.string().nullable(),
  pause_reason: z.string().nullable(),
  next_send_at: z.number().nullable(),
})
export const messageEventSchema = z.object({
  campaign_id: z.number(),
  lead_id: z.number(),
  message_id: z.number(),
  kind: z.enum(['initial', 'followup_1', 'followup_2']),
  state: z.enum(['queued', 'sending', 'sent', 'failed', 'needs_confirmation', 'cancelled']),
})
export const inboxEventSchema = z.object({
  lead_id: z.number(),
  event_id: z.number(),
  type: z.enum(['reply', 'bounce', 'auto_reply', 'opt_out']),
})
export const sendingEventSchema = z.object({
  paused_all: z.boolean(),
  sent_today: z.number(),
  cap: z.number(),
})

export type CampaignEvent = z.infer<typeof campaignEventSchema>
export type InboxEvent = z.infer<typeof inboxEventSchema>
export type SendingEvent = z.infer<typeof sendingEventSchema>
