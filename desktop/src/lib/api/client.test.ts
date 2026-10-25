import { describe, expect, it } from 'vitest'
import { mockApi } from '@/test/utils'
import { api, ApiError } from './client'

describe('api client', () => {
  it('sends the bearer token on every request', async () => {
    const { calls } = mockApi({ 'GET /health': { json: { status: 'ok' } } })
    await api.get('/health')
    expect(calls[0]?.headers.get('Authorization')).toBe('Bearer test-token')
  })

  it('serialises JSON bodies, path params and query strings', async () => {
    const { calls } = mockApi({
      'PUT /settings': { json: {} },
      'POST /jobs/7/cancel': { json: {} },
      'GET /suppression': { json: [] },
    })
    await api.put('/settings', { body: { daily_cap: 20 } })
    await api.post('/jobs/{job_id}/cancel', { params: { job_id: 7 } })
    await api.get('/suppression', { query: { q: 'a b', } })
    expect(calls[0]?.body).toEqual({ daily_cap: 20 })
    expect(calls[0]?.headers.get('Content-Type')).toBe('application/json')
    expect(calls[1]?.path).toBe('/jobs/7/cancel')
    expect(calls[2]?.path).toBe('/suppression?q=a+b')
  })

  it('omits empty query values', async () => {
    const { calls } = mockApi({ 'GET /suppression': { json: [] } })
    await api.get('/suppression', { query: { q: '' } })
    expect(calls[0]?.path).toBe('/suppression')
  })

  it('maps FastAPI string errors', async () => {
    mockApi({ 'GET /health': { status: 403, json: { detail: 'Forbidden origin' } } })
    await expect(api.get('/health')).rejects.toMatchObject({ status: 403, detail: 'Forbidden origin' })
  })

  it('maps 422 validation arrays to per-field errors', async () => {
    mockApi({
      'PUT /settings': {
        status: 422,
        json: { detail: [{ loc: ['body', 'window_end'], msg: 'window_end must be after window_start' }, { loc: ['body', 'num_ctx'], msg: 'bad' }] },
      },
    })
    const err = await api.put('/settings', { body: {} }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).fieldErrors).toEqual({ window_end: 'window_end must be after window_start', num_ctx: 'bad' })
    expect((err as ApiError).status).toBe(422)
  })

  it('returns undefined for 204', async () => {
    mockApi({ 'DELETE /suppression': { status: 204 } })
    await expect(api.delete('/suppression', { query: { email: 'a@x.com', confirm: 'a@x.com' } })).resolves.toBeUndefined()
  })

  it('turns a network failure into a friendly ApiError(0)', async () => {
    mockApi({ 'GET /health': () => { throw new TypeError('Failed to fetch') } })
    const err = await api.get('/health').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(0)
    expect((err as ApiError).detail).toMatch(/Cannot reach the backend/)
  })
})
