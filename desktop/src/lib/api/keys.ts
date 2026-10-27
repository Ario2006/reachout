/** Query-key prefixes shared by every feature's hooks and by the SSE router (lib/events.tsx), which invalidates by
 *  prefix. Hooks append their own filters after the prefix, e.g. [...K.drafts, { campaignId, status }]. */
export const K = {
  templates: ['templates'] as const,
  profile: ['profile'] as const,
  drafts: ['drafts'] as const,
  campaigns: ['campaigns'] as const,
  campaign: (id: number) => ['campaigns', id] as const,
  sending: ['sending'] as const,
  threads: ['threads'] as const,
  inboxStatus: ['inbox-status'] as const,
  metrics: ['metrics'] as const,
  activity: ['activity'] as const,
  followups: ['followups'] as const,
}
