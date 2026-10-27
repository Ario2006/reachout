/** Pure rules the Electron main process uses to decide what closing and quitting mean while a campaign is sending.
 *  Kept free of Electron imports so they can be unit-tested. */

export interface SendingSnapshot {
  /** At least one campaign is in the running state. */
  anyRunning: boolean
  /** The global pause-all switch is on: nothing is actually sent. */
  pausedAll: boolean
}

export const IDLE: SendingSnapshot = { anyRunning: false, pausedAll: false }

export function isSending(s: SendingSnapshot): boolean {
  return s.anyRunning && !s.pausedAll
}

/** Closing the window while sending hides it to the menu bar; otherwise it closes (and the app quits) as before. */
export function closeAction(sending: boolean, quitting: boolean): 'hide' | 'close' {
  return sending && !quitting ? 'hide' : 'close'
}

/** Text next to the menu bar icon. */
export function trayTitle(s: SendingSnapshot): string {
  if (isSending(s)) return 'Sending'
  return s.pausedAll ? 'Paused' : 'Idle'
}

interface CampaignLike {
  state: string
}

/** Build a snapshot from the two API answers; anything unreadable counts as "not sending" (never blocks quitting). */
export function snapshotFrom(campaigns: unknown, status: unknown): SendingSnapshot {
  const list = Array.isArray(campaigns) ? (campaigns as CampaignLike[]) : []
  const anyRunning = list.some((c) => typeof c === 'object' && c !== null && c.state === 'running')
  const pausedAll = typeof status === 'object' && status !== null && (status as { paused_all?: unknown }).paused_all === true
  return { anyRunning, pausedAll }
}
