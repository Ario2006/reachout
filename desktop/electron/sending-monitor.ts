import type { BackendInfo } from '../shared/bridge'
import { IDLE, snapshotFrom, type SendingSnapshot } from '../shared/sending-guard'
import { log } from './logger'

const TIMEOUT_MS = 4000

/** Asks the local API whether a campaign is sending, and pauses everything on request. Errors never throw: if the
 *  backend cannot be reached nothing is sending, and quitting must stay possible. */
export class SendingMonitor {
  constructor(private readonly info: () => BackendInfo) {}

  private async call(method: 'GET' | 'POST', path: string): Promise<unknown> {
    const { baseUrl, token } = this.info()
    const res = await fetch(baseUrl + path, {
      method,
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    })
    if (!res.ok) throw new Error(`${path} answered ${res.status}`)
    return (await res.json()) as unknown
  }

  async snapshot(): Promise<SendingSnapshot> {
    try {
      const [campaigns, status] = await Promise.all([this.call('GET', '/campaigns'), this.call('GET', '/sending/status')])
      return snapshotFrom(campaigns, status)
    } catch {
      return IDLE
    }
  }

  async pauseAll(): Promise<boolean> {
    try {
      await this.call('POST', '/sending/pause-all')
      return true
    } catch (e) {
      log('error', `pause-all failed: ${e instanceof Error ? e.message : String(e)}`)
      return false
    }
  }
}
