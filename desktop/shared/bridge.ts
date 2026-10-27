/** Contract between the Electron main process, the preload script and the renderer. */

export interface BackendInfo {
  baseUrl: string
  token: string
}

export type BackendState = 'starting' | 'ready' | 'restarting' | 'failed'

export interface BackendStatus {
  state: BackendState
  message: string
}

export interface AppInfo {
  version: string
  electron: string
  platform: string
  dataDir: string
  logsDir: string
}

export type ThemePreference = 'system' | 'light' | 'dark'

export type MenuAction = { type: 'navigate'; route: string } | { type: 'command-palette' }

export interface ReachoutBridge {
  getBackend(): Promise<BackendInfo>
  getBackendStatus(): Promise<BackendStatus>
  onBackendStatus(cb: (status: BackendStatus) => void): () => void
  onMenu(cb: (action: MenuAction) => void): () => void
  openExternal(url: string): Promise<void>
  openLogs(): Promise<void>
  openDataFolder(): Promise<void>
  getAppInfo(): Promise<AppInfo>
  setTheme(theme: ThemePreference): Promise<void>
  retryBackend(): Promise<void>
  quit(): Promise<void>
  /** Native file dialog for the campaign attachment; resolves to the chosen PDF path or null when cancelled. */
  pickPdf(): Promise<string | null>
}

export const CH = {
  getBackend: 'backend:get',
  getBackendStatus: 'backend:status:get',
  backendStatus: 'backend:status',
  menu: 'menu:action',
  openExternal: 'shell:open-external',
  openLogs: 'app:open-logs',
  openDataFolder: 'app:open-data',
  getAppInfo: 'app:info',
  setTheme: 'theme:set',
  retryBackend: 'backend:retry',
  quit: 'app:quit',
  pickPdf: 'dialog:pick-pdf',
} as const
