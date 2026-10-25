import type { BackendInfo } from '@shared/bridge'

let cached: Promise<BackendInfo> | null = null

async function resolveConnection(): Promise<BackendInfo> {
  if (window.reachout) return window.reachout.getBackend()
  // Browser-only development / tests: point at a manually started backend.
  const baseUrl: unknown = import.meta.env['VITE_API_URL']
  const token: unknown = import.meta.env['VITE_API_TOKEN']
  if (typeof baseUrl === 'string' && typeof token === 'string') return { baseUrl, token }
  throw new Error('No backend connection: run the app through Electron (scripts/run.sh).')
}

/** Base URL and bearer token of this launch's backend (cached for the session). */
export function getConnection(): Promise<BackendInfo> {
  cached ??= resolveConnection().catch((e: unknown) => {
    cached = null // allow a retry
    throw e
  })
  return cached
}

/** Test seam. */
export function setConnectionForTests(info: BackendInfo | null): void {
  cached = info ? Promise.resolve(info) : null
}
