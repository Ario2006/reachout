import { getConnection } from '@/lib/backend'
import type { paths } from './schema'

type Method = 'get' | 'post' | 'put' | 'delete'
type PathsWith<M extends Method> = {
  [P in keyof paths]: paths[P] extends Record<M, unknown> ? P : never
}[keyof paths]
type Op<P extends keyof paths, M extends Method> = paths[P] extends Record<M, infer O> ? O : never

type Ok = 200 | 201 | 202 | 204
type JsonResponse<O> = O extends { responses: infer R }
  ? {
      [S in keyof R & Ok]: R[S] extends { content: { 'application/json': infer B } } ? B : undefined
    }[keyof R & Ok]
  : never
type JsonBody<O> = O extends { requestBody?: infer B }
  ? B extends { content: { 'application/json': infer J } }
    ? J
    : undefined
  : undefined
type PathParams<O> = O extends { parameters: { path: infer P } } ? P : never
type QueryParams<O> = O extends { parameters: { query?: infer Q } } ? Q : never

export interface RequestOptions<O> {
  params?: PathParams<O>
  query?: QueryParams<O>
  body?: JsonBody<O>
  signal?: AbortSignal
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    /** Field name -> message, for 422 validation errors. */
    readonly fieldErrors: Record<string, string> = {},
  ) {
    super(detail)
    this.name = 'ApiError'
  }
}

interface FastApiIssue {
  loc?: unknown[]
  msg?: string
}

function parseError(status: number, body: unknown): ApiError {
  const detail = typeof body === 'object' && body !== null && 'detail' in body ? body.detail : undefined
  if (typeof detail === 'string') return new ApiError(status, detail)
  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {}
    const messages: string[] = []
    for (const raw of detail as FastApiIssue[]) {
      const msg = raw.msg ?? 'Invalid value'
      const field = raw.loc && raw.loc.length > 1 ? String(raw.loc[raw.loc.length - 1]) : undefined
      if (field) fields[field] = msg
      messages.push(field ? `${field}: ${msg}` : msg)
    }
    return new ApiError(status, messages.join('; ') || 'Invalid request', fields)
  }
  return new ApiError(status, `Request failed (${status})`)
}

function buildUrl(base: string, template: string, params: unknown, query: unknown): string {
  let path = template
  if (params && typeof params === 'object') {
    for (const [k, v] of Object.entries(params)) {
      path = path.replace(`{${k}}`, encodeURIComponent(String(v)))
    }
  }
  const qs = new URLSearchParams()
  if (query && typeof query === 'object') {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    }
  }
  const s = qs.toString()
  return base + path + (s ? `?${s}` : '')
}

export async function request<P extends keyof paths, M extends Method>(
  method: M,
  path: P,
  opts: RequestOptions<Op<P, M>> = {},
): Promise<JsonResponse<Op<P, M>>> {
  const { baseUrl, token } = await getConnection()
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
  const init: RequestInit = { method: method.toUpperCase(), headers, signal: opts.signal ?? null }
  if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(opts.body)
  }
  let res: Response
  try {
    res = await fetch(buildUrl(baseUrl, path, opts.params, opts.query), init)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, 'Cannot reach the backend. It may be restarting.')
  }
  if (res.status === 204) return undefined as JsonResponse<Op<P, M>>
  const text = await res.text()
  let json: unknown
  try {
    json = text ? JSON.parse(text) : undefined
  } catch {
    json = undefined
  }
  if (!res.ok) throw parseError(res.status, json)
  return json as JsonResponse<Op<P, M>>
}

export const api = {
  get: <P extends PathsWith<'get'>>(path: P, opts?: RequestOptions<Op<P, 'get'>>) =>
    request('get', path, opts),
  post: <P extends PathsWith<'post'>>(path: P, opts?: RequestOptions<Op<P, 'post'>>) =>
    request('post', path, opts),
  put: <P extends PathsWith<'put'>>(path: P, opts?: RequestOptions<Op<P, 'put'>>) =>
    request('put', path, opts),
  delete: <P extends PathsWith<'delete'>>(path: P, opts?: RequestOptions<Op<P, 'delete'>>) =>
    request('delete', path, opts),
}
