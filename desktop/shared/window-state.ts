/** Window geometry persistence: the pure parts, so they can be unit-tested without Electron. */
export interface Rect { x: number; y: number; width: number; height: number }
export interface SavedWindow extends Rect { maximized: boolean }

export const MIN_SIZE = { width: 960, height: 620 }

/** Parse whatever is on disk; anything malformed yields null (fall back to the default size). */
export function parseSaved(raw: unknown): SavedWindow | null {
  if (typeof raw !== 'object' || raw === null) return null
  const r = raw as Record<string, unknown>
  const num = (k: string): number | null => (typeof r[k] === 'number' && Number.isFinite(r[k]) ? (r[k] as number) : null)
  const x = num('x'), y = num('y'), width = num('width'), height = num('height')
  if (x === null || y === null || width === null || height === null) return null
  if (width < MIN_SIZE.width || height < MIN_SIZE.height) return null
  return { x, y, width, height, maximized: r['maximized'] === true }
}

function overlap(a: Rect, b: Rect): number {
  const w = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)
  const h = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y)
  return w > 0 && h > 0 ? w * h : 0
}

/** Use the saved bounds only if at least a usable part of the window is still on a connected display. */
export function fitToDisplays(saved: SavedWindow | null, displays: Rect[]): SavedWindow | null {
  if (!saved) return null
  const visible = displays.reduce((n, d) => Math.max(n, overlap(saved, d)), 0)
  return visible >= 200 * 200 ? saved : null
}
