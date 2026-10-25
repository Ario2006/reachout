import { zodResolver } from '@hookform/resolvers/zod'
import { Controller, useForm } from 'react-hook-form'
import { z } from 'zod'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
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
    },
  })
  const { submit, warnings, saving } = useSectionSave(form)
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
