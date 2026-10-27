import { zodResolver } from '@hookform/resolvers/zod'
import { Controller, useForm } from 'react-hook-form'
import { z } from 'zod'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { SettingsResponse } from '@/lib/api/types'
import { SaveBar, Section, Warnings } from '../form-bits'
import { useSectionSave } from '../use-section-save'

const num = (min: number, max: number) =>
  z.number({ error: 'Enter a number' }).min(min, { error: `At least ${min}` }).max(max, { error: `At most ${max}` })

const schema = z.object({
  max_pages_per_domain: num(1, 20),
  crawl_delay_min_s: num(0.5, 30),
  crawl_delay_max_s: num(0.5, 30),
  fetch_timeout_s: num(5, 60),
  search_source: z.enum(['ddgs', 'searxng']),
  searxng_url: z.string().trim(),
  global_concurrency: num(1, 64),
  page_cache_ttl_days: num(1, 90),
  crawler_contact: z.string().trim().max(200, { error: 'At most 200 characters' }),
  browser_tier_enabled: z.boolean(),
  max_domains_per_run: num(1, 300),
  max_leads_per_site: num(1, 25),
  target_leads: num(0, 500),
  blocklist_text: z.string(),
}).refine((v) => v.crawl_delay_max_s >= v.crawl_delay_min_s, { path: ['crawl_delay_max_s'], error: 'Must be at least the minimum' })
  .refine((v) => v.search_source !== 'searxng' || v.searxng_url !== '', { path: ['searxng_url'], error: 'Required when SearXNG is the search source' })
type Values = z.infer<typeof schema>

export function CrawlingTab({ data }: { data: SettingsResponse }) {
  const s = data.settings
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    values: {
      max_pages_per_domain: s.max_pages_per_domain, crawl_delay_min_s: s.crawl_delay_min_s,
      crawl_delay_max_s: s.crawl_delay_max_s, fetch_timeout_s: s.fetch_timeout_s,
      search_source: s.search_source === 'searxng' ? 'searxng' : 'ddgs', searxng_url: s.searxng_url,
      global_concurrency: s.global_concurrency ?? 30, page_cache_ttl_days: s.page_cache_ttl_days ?? 14,
      crawler_contact: s.crawler_contact ?? '', browser_tier_enabled: s.browser_tier_enabled ?? false, max_domains_per_run: s.max_domains_per_run ?? 60, max_leads_per_site: s.max_leads_per_site ?? 5, target_leads: s.target_leads ?? 30,
      blocklist_text: (s.domain_blocklist_extra ?? []).join('\n'),
    },
  })
  const { submit, warnings, saving } = useSectionSave(form, ({ blocklist_text, ...rest }) => ({
    ...rest,
    domain_blocklist_extra: blocklist_text.split(/[\s,]+/).filter(Boolean),
  }))
  const e = form.formState.errors
  const source = form.watch('search_source')

  return (
    <form onSubmit={submit} noValidate className="space-y-6">
      <Section title="Crawling" description="Polite by default: robots.txt is always respected.">
        <FieldGroup>
          <div className="grid grid-cols-2 gap-4">
            <Field data-invalid={!!e.max_pages_per_domain}>
              <FieldLabel htmlFor="max-pages">Max pages per domain</FieldLabel>
              <Input id="max-pages" type="number" aria-invalid={!!e.max_pages_per_domain} {...form.register('max_pages_per_domain', { valueAsNumber: true })} />
              <FieldError errors={[e.max_pages_per_domain]} />
            </Field>
            <Field data-invalid={!!e.fetch_timeout_s}>
              <FieldLabel htmlFor="timeout">Fetch timeout (s)</FieldLabel>
              <Input id="timeout" type="number" aria-invalid={!!e.fetch_timeout_s} {...form.register('fetch_timeout_s', { valueAsNumber: true })} />
              <FieldError errors={[e.fetch_timeout_s]} />
            </Field>
            <Field data-invalid={!!e.crawl_delay_min_s}>
              <FieldLabel htmlFor="crawl-min">Delay per domain, min (s)</FieldLabel>
              <Input id="crawl-min" type="number" step="0.5" aria-invalid={!!e.crawl_delay_min_s} {...form.register('crawl_delay_min_s', { valueAsNumber: true })} />
              <FieldError errors={[e.crawl_delay_min_s]} />
            </Field>
            <Field data-invalid={!!e.crawl_delay_max_s}>
              <FieldLabel htmlFor="crawl-max">Delay per domain, max (s)</FieldLabel>
              <Input id="crawl-max" type="number" step="0.5" aria-invalid={!!e.crawl_delay_max_s} {...form.register('crawl_delay_max_s', { valueAsNumber: true })} />
              <FieldError errors={[e.crawl_delay_max_s]} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field data-invalid={!!e.max_domains_per_run}>
              <FieldLabel htmlFor="max-domains">Sites per search</FieldLabel>
              <Input id="max-domains" type="number" aria-invalid={!!e.max_domains_per_run} {...form.register('max_domains_per_run', { valueAsNumber: true })} />
              <FieldDescription>The most sites one search will crawl.</FieldDescription>
              <FieldError errors={[e.max_domains_per_run]} />
            </Field>
            <Field data-invalid={!!e.global_concurrency}>
              <FieldLabel htmlFor="concurrency">Sites crawled at once</FieldLabel>
              <Input id="concurrency" type="number" aria-invalid={!!e.global_concurrency} {...form.register('global_concurrency', { valueAsNumber: true })} />
              <FieldDescription>Each site still gets one request at a time, paced by the delay above. 30 suits an 8 GB Mac.</FieldDescription>
              <FieldError errors={[e.global_concurrency]} />
            </Field>
          </div>
          <Field data-invalid={!!e.target_leads}>
            <FieldLabel htmlFor="target-leads">Aim for this many leads per search</FieldLabel>
            <Input id="target-leads" type="number" className="w-32" aria-invalid={!!e.target_leads} {...form.register('target_leads', { valueAsNumber: true })} />
            <FieldDescription>
              If a search finds fewer, it keeps going with new wording and more list pages (up to about 12 minutes or the site limit).
              Set 0 to run each search once.
            </FieldDescription>
            <FieldError errors={[e.target_leads]} />
          </Field>
          <Field data-invalid={!!e.max_leads_per_site}>
            <FieldLabel htmlFor="max-leads-site">Leads kept per site</FieldLabel>
            <Input id="max-leads-site" type="number" className="w-32" aria-invalid={!!e.max_leads_per_site} {...form.register('max_leads_per_site', { valueAsNumber: true })} />
            <FieldDescription>
              A large company's contact page can list dozens of regional mailboxes. Each site keeps its best few for your
              kind of outreach, for example careers@ and jobs@ before sales@ when looking for internships.
            </FieldDescription>
            <FieldError errors={[e.max_leads_per_site]} />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field data-invalid={!!e.page_cache_ttl_days}>
              <FieldLabel htmlFor="cache-ttl">Keep fetched pages for (days)</FieldLabel>
              <Input id="cache-ttl" type="number" aria-invalid={!!e.page_cache_ttl_days} {...form.register('page_cache_ttl_days', { valueAsNumber: true })} />
              <FieldDescription>A repeat search reuses saved pages instead of fetching them again.</FieldDescription>
              <FieldError errors={[e.page_cache_ttl_days]} />
            </Field>
            <Field data-invalid={!!e.crawler_contact}>
              <FieldLabel htmlFor="crawler-contact">Crawler contact</FieldLabel>
              <Input id="crawler-contact" placeholder="https://your-site.example or you@example.com" aria-invalid={!!e.crawler_contact} {...form.register('crawler_contact')} />
              <FieldDescription>Shown to site owners in the crawler's User-Agent so they can reach you. Never filled in automatically.</FieldDescription>
              <FieldError errors={[e.crawler_contact]} />
            </Field>
          </div>
          <Field orientation="horizontal">
            <Controller control={form.control} name="browser_tier_enabled" render={({ field }) => (
              <Switch id="browser-tier" checked={field.value} onCheckedChange={field.onChange} />
            )} />
            <div className="space-y-1">
              <FieldLabel htmlFor="browser-tier">Render JavaScript-only pages</FieldLabel>
              <FieldDescription>
                Uses a headless browser, at most two pages at a time, only for pages that arrive empty. Off by default and skipped
                cleanly if Playwright's Chromium is not installed (<code>.venv/bin/python -m playwright install chromium</code>).
              </FieldDescription>
            </div>
          </Field>
          <Field>
            <FieldLabel htmlFor="blocklist">Sites to never crawl</FieldLabel>
            <Textarea id="blocklist" rows={4} placeholder={'example.com\nanother-site.org'} className="font-mono text-xs" {...form.register('blocklist_text')} />
            <FieldDescription>
              One domain per line, in addition to the built-in list (social networks, job boards, news and directory sites, code hosts,
              universities and government sites), which cannot be edited.
            </FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="search-source">Search source</FieldLabel>
            <Controller control={form.control} name="search_source" render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger id="search-source" className="w-72"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ddgs">DuckDuckGo (default)</SelectItem>
                  <SelectItem value="searxng">Local SearXNG</SelectItem>
                </SelectContent>
              </Select>
            )} />
          </Field>
          {source === 'searxng' ? (
            <Field data-invalid={!!e.searxng_url}>
              <FieldLabel htmlFor="searxng-url">SearXNG URL</FieldLabel>
              <Input id="searxng-url" placeholder="http://localhost:8080" aria-invalid={!!e.searxng_url} {...form.register('searxng_url')} />
              <FieldDescription>A SearXNG instance you run yourself.</FieldDescription>
              <FieldError errors={[e.searxng_url]} />
            </Field>
          ) : null}
        </FieldGroup>
      </Section>
      <Warnings items={warnings} />
      <SaveBar saving={saving} dirty={form.formState.isDirty} />
    </form>
  )
}
