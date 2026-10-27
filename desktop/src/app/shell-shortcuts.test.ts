import { describe, expect, it } from 'vitest'
import { GO_ROUTES } from '@shared/go-routes'
import { fitToDisplays, parseSaved } from '@shared/window-state'
import { NAV } from './nav'

describe('native Go menu', () => {
  it('lists every sidebar screen in sidebar order, so Cmd+1..7 match the sidebar', () => {
    expect(GO_ROUTES.map((g) => g.route)).toEqual(NAV.map((n) => n.to))
    expect(GO_ROUTES.map((g) => g.label)).toEqual(NAV.map((n) => n.label))
  })
})

describe('window state', () => {
  const screen1 = { x: 0, y: 0, width: 1440, height: 900 }
  const ok = { x: 100, y: 80, width: 1240, height: 820, maximized: false }

  it('parses a saved window and rejects junk or tiny sizes', () => {
    expect(parseSaved(ok)).toEqual(ok)
    expect(parseSaved({ ...ok, maximized: 'yes' })).toEqual(ok)
    expect(parseSaved(null)).toBeNull()
    expect(parseSaved('x')).toBeNull()
    expect(parseSaved({ ...ok, width: 'wide' })).toBeNull()
    expect(parseSaved({ ...ok, width: 100 })).toBeNull()
    expect(parseSaved({ ...ok, x: Number.NaN })).toBeNull()
  })

  it('restores only when the window is still on a connected display', () => {
    expect(fitToDisplays(ok, [screen1])).toEqual(ok)
    expect(fitToDisplays({ ...ok, x: 3000 }, [screen1])).toBeNull() // second monitor unplugged
    expect(fitToDisplays({ ...ok, x: 3000 }, [screen1, { x: 2560, y: 0, width: 1920, height: 1080 }])).not.toBeNull()
    expect(fitToDisplays(null, [screen1])).toBeNull()
  })
})
