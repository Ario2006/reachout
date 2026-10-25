import type { ReachoutBridge } from '@shared/bridge'

declare global {
  interface Window {
    /** Present only inside Electron (exposed by electron/preload.ts). */
    reachout?: ReachoutBridge
  }
}

export {}
