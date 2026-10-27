import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { components } from '@/lib/api/schema'
import { setConnectionForTests } from '@/lib/backend'

export const BASE = 'http://backend.test'

export type SettingsResponse = components['schemas']['SettingsResponse']

export function settingsFixture(over: Partial<components['schemas']['AppSettings']> = {}, extra: Partial<SettingsResponse> = {}): SettingsResponse {
  return {
    settings: {
      gmail_address: '', display_name: '', footer_text: "If you'd rather not hear from me, just reply 'stop'.",
      ollama_model: 'qwen2.5:3b-instruct', num_ctx: 4096, keep_alive: '5m',
      daily_cap: 25, delay_min_s: 60, delay_max_s: 180, window_start: '09:00', window_end: '18:00',
      send_days: [0, 1, 2, 3, 4, 5], allow_guessed_emails: false,
      max_per_domain_per_day: 2, min_minutes_between_same_domain: 60, use_recipient_timezone: true,
      max_pages_per_domain: 6, crawl_delay_min_s: 1, crawl_delay_max_s: 2, fetch_timeout_s: 15,
      search_source: 'ddgs', searxng_url: '', global_concurrency: 30, page_cache_ttl_days: 14, crawler_contact: '', browser_tier_enabled: false, max_domains_per_run: 60, max_leads_per_site: 5, target_leads: 30, domain_blocklist_extra: [],
      first_run_complete: true, legal_acknowledged: true,
      ...over,
    },
    gmail_password_set: false, warnings: [], notices: [], limits: { hard_daily_ceiling: 100 },
    ...extra,
  }
}

/** JSON bodies are parsed; anything else (e.g. CSV) is kept as text. */
function parseBody(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown
  } catch {
    return raw
  }
}

export interface Call { method: string; path: string; body: unknown; headers: Headers }
type Handler = (call: Call) => { status?: number; json?: unknown } | undefined

/** Replace fetch with a router keyed by "METHOD /path". Unmatched requests fail the test loudly. */
export function mockApi(routes: Record<string, Handler | { status?: number; json?: unknown }>) {
  setConnectionForTests({ baseUrl: BASE, token: 'test-token' })
  const calls: Call[] = []
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url)
    const method = (init?.method ?? 'GET').toUpperCase()
    const key = `${method} ${url.pathname}`
    const call: Call = {
      method, path: url.pathname + url.search,
      body: typeof init?.body === 'string' ? parseBody(init.body) : undefined,
      headers: new Headers(init?.headers),
    }
    calls.push(call)
    const route = routes[key]
    if (route === undefined) throw new Error(`Unmocked request: ${key}`)
    const res = typeof route === 'function' ? route(call) : route
    const status = res?.status ?? 200
    return new Response(status === 204 ? null : JSON.stringify(res?.json ?? {}), {
      status, headers: { 'content-type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', impl)
  return { calls, impl }
}

export function renderWithProviders(ui: ReactElement, route = '/'): RenderResult & { client: QueryClient } {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } } })
  const result = render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return Object.assign(result, { client })
}
