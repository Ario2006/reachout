/** jsdom has no layout. Give every element a viewport so the virtualiser renders rows. */
export function stubViewport(width = 1400, height = 600): void {
  for (const [prop, value] of [['clientWidth', width], ['clientHeight', height], ['offsetWidth', width], ['offsetHeight', height]] as const) {
    Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get: () => value })
  }
}
