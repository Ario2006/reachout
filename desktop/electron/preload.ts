import { contextBridge, ipcRenderer } from 'electron'
import { CH, type BackendStatus, type MenuAction, type ReachoutBridge } from '../shared/bridge'

function subscribe<T>(channel: string, cb: (payload: T) => void): () => void {
  const listener = (_e: Electron.IpcRendererEvent, payload: T): void => cb(payload)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

const bridge: ReachoutBridge = {
  getBackend: () => ipcRenderer.invoke(CH.getBackend),
  getBackendStatus: () => ipcRenderer.invoke(CH.getBackendStatus),
  onBackendStatus: (cb) => subscribe<BackendStatus>(CH.backendStatus, cb),
  onMenu: (cb) => subscribe<MenuAction>(CH.menu, cb),
  openExternal: (url) => ipcRenderer.invoke(CH.openExternal, url),
  openLogs: () => ipcRenderer.invoke(CH.openLogs),
  openDataFolder: () => ipcRenderer.invoke(CH.openDataFolder),
  getAppInfo: () => ipcRenderer.invoke(CH.getAppInfo),
  setTheme: (theme) => ipcRenderer.invoke(CH.setTheme, theme),
  retryBackend: () => ipcRenderer.invoke(CH.retryBackend),
  quit: () => ipcRenderer.invoke(CH.quit),
}

contextBridge.exposeInMainWorld('reachout', bridge)
