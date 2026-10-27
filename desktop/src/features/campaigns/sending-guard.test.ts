import { describe, expect, it } from 'vitest'
import { closeAction, isSending, snapshotFrom, trayTitle } from '@shared/sending-guard'

describe('sending guard (close hides, quit asks)', () => {
  it('hides to the menu bar only while a campaign is really sending', () => {
    expect(closeAction(true, false)).toBe('hide')
    expect(closeAction(false, false)).toBe('close')
    expect(closeAction(true, true)).toBe('close') // an explicit quit is never blocked by the hide rule
  })

  it('treats pause-all as not sending', () => {
    expect(isSending({ anyRunning: true, pausedAll: false })).toBe(true)
    expect(isSending({ anyRunning: true, pausedAll: true })).toBe(false)
    expect(isSending({ anyRunning: false, pausedAll: false })).toBe(false)
    expect(trayTitle({ anyRunning: true, pausedAll: false })).toBe('Sending')
    expect(trayTitle({ anyRunning: true, pausedAll: true })).toBe('Paused')
    expect(trayTitle({ anyRunning: false, pausedAll: false })).toBe('Idle')
  })

  it('reads the API answers and never throws on junk', () => {
    expect(snapshotFrom([{ state: 'paused' }, { state: 'running' }], { paused_all: false })).toEqual({ anyRunning: true, pausedAll: false })
    expect(snapshotFrom([{ state: 'done' }], { paused_all: true })).toEqual({ anyRunning: false, pausedAll: true })
    expect(snapshotFrom(null, undefined)).toEqual({ anyRunning: false, pausedAll: false })
    expect(snapshotFrom([null, 3], 'x')).toEqual({ anyRunning: false, pausedAll: false })
  })
})
