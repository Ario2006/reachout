import { pathToFileURL } from 'node:url'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve, sep } from 'node:path'
import {
  BrowserWindow, Menu, app, dialog, ipcMain, nativeTheme, net, protocol, screen, session, shell,
  type IpcMainInvokeEvent,
} from 'electron'
import { CH, type AppInfo, type BackendStatus, type MenuAction, type ThemePreference } from '../shared/bridge'
import { BackendSupervisor } from './backend-supervisor'
import { buildCsp } from './csp'
import { log } from './logger'
import { buildMenu } from './menu'
import { dataDir, logsDir, pythonPath, repoRoot } from './paths'
import { SendingMonitor } from './sending-monitor'
import { splashHtml } from './splash'
import { TrayController } from './tray'
import { fitToDisplays, parseSaved, type SavedWindow } from '../shared/window-state'
import { closeAction, isSending, IDLE, type SendingSnapshot } from '../shared/sending-guard'

const DEV_URL = process.env['ELECTRON_RENDERER_URL'] ?? null
const isDev = DEV_URL !== null
const APP_SCHEME = 'reachout'
const APP_ORIGIN = isDev && DEV_URL ? new URL(DEV_URL).origin : `${APP_SCHEME}://app`
const RENDERER_DIR = resolve(__dirname, '../renderer')

app.setName('Reachout')
protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
])

let mainWin: BrowserWindow | null = null
let splashWin: BrowserWindow | null = null
let quitting = false
let skipQuitConfirm = false
let lastSending: SendingSnapshot = IDLE
let monitorTimer: NodeJS.Timeout | null = null

const supervisor = new BackendSupervisor({
  python: pythonPath(),
  repoRoot: repoRoot(),
  logsDir: logsDir(),
  allowedOrigin: APP_ORIGIN,
  reload: isDev,
})

const monitor = new SendingMonitor(() => supervisor.info)
const tray = new TrayController({
  show: () => showMainWindow(),
  pauseAll: () => void monitor.pauseAll().then(() => refreshSending()),
  quit: () => app.quit(),
})

function showMainWindow(): void {
  if (mainWin && !mainWin.isDestroyed()) {
    if (mainWin.isMinimized()) mainWin.restore()
    mainWin.show()
    mainWin.focus()
  }
  tray.destroy()
}

/** Poll the API while the app runs: a hidden window with nothing left to send comes back (no invisible app). */
async function refreshSending(): Promise<void> {
  lastSending = await monitor.snapshot()
  const hidden = mainWin !== null && !mainWin.isDestroyed() && !mainWin.isVisible()
  if (!hidden) return
  if (isSending(lastSending) || lastSending.pausedAll) tray.update(lastSending)
  else showMainWindow()
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    const w = mainWin ?? splashWin
    if (w) {
      if (w.isMinimized()) w.restore()
      w.focus()
    }
  })
}

// ---------------------------------------------------------------- windows

const PRELOAD = join(__dirname, '../preload/preload.js')
const webPreferences = {
  preload: PRELOAD,
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
}

function createSplash(): BrowserWindow {
  const w = new BrowserWindow({
    width: 420, height: 280, resizable: false, minimizable: false, maximizable: false,
    fullscreenable: false, frame: false, show: false, center: true, webPreferences,
  })
  w.once('ready-to-show', () => w.show())
  w.on('closed', () => { splashWin = null })
  void w.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(splashHtml()))
  return w
}

const windowStateFile = (): string => join(dataDir(), 'window-state.json')

function loadWindowState(): SavedWindow | null {
  try {
    const file = windowStateFile()
    if (!existsSync(file)) return null
    return fitToDisplays(parseSaved(JSON.parse(readFileSync(file, 'utf8')) as unknown), screen.getAllDisplays().map((d) => d.workArea))
  } catch {
    return null // unreadable or corrupt: start with the default size
  }
}

function saveWindowState(w: BrowserWindow): void {
  try {
    if (w.isDestroyed() || w.isMinimized() || w.isFullScreen()) return
    const state: SavedWindow = { ...w.getNormalBounds(), maximized: w.isMaximized() }
    mkdirSync(dirname(windowStateFile()), { recursive: true })
    writeFileSync(windowStateFile(), JSON.stringify(state))
  } catch (e) {
    log('warn', `could not save the window position: ${e instanceof Error ? e.message : String(e)}`)
  }
}

function createMainWindow(): BrowserWindow {
  const saved = loadWindowState()
  const w = new BrowserWindow({
    width: saved?.width ?? 1240, height: saved?.height ?? 820, minWidth: 960, minHeight: 620, show: false,
    ...(saved ? { x: saved.x, y: saved.y } : {}),
    titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 16, y: 18 },
    vibrancy: 'sidebar', visualEffectState: 'followWindow', backgroundColor: '#00000000',
    webPreferences,
  })
  if (saved?.maximized) w.maximize()
  let saveTimer: NodeJS.Timeout | null = null
  const saveSoon = () => {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => saveWindowState(w), 500)
  }
  w.on('resize', saveSoon)
  w.on('move', saveSoon)
  w.once('ready-to-show', () => {
    w.show()
    splashWin?.close()
  })
  w.on('close', (event) => {
    if (saveTimer) clearTimeout(saveTimer)
    saveWindowState(w)
    if (closeAction(isSending(lastSending), quitting) === 'hide') {
      event.preventDefault()  // keep sending: hide to the menu bar instead of quitting
      w.hide()
      tray.update(lastSending)
      log('info', 'window hidden to the menu bar: a campaign is sending')
    }
  })
  w.on('closed', () => { mainWin = null })
  void w.loadURL(isDev && DEV_URL ? DEV_URL : `${APP_SCHEME}://app/index.html`)
  return w
}

function sendToMain(channel: string, payload: unknown): void {
  if (mainWin && !mainWin.isDestroyed()) mainWin.webContents.send(channel, payload)
}

// --------------------------------------------------------------- security

function isOurWindow(e: IpcMainInvokeEvent, main = false): boolean {
  const id = e.sender.id
  const m = mainWin && !mainWin.isDestroyed() && mainWin.webContents.id === id
  const s = splashWin && !splashWin.isDestroyed() && splashWin.webContents.id === id
  return main ? Boolean(m) : Boolean(m || s)
}

function hardenSession(): void {
  const csp = buildCsp(isDev ? APP_ORIGIN : null)
  if (isDev) {
    session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
      cb({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [csp] } })
    })
  }
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false))
  session.defaultSession.setPermissionCheckHandler(() => false)

  // Serve the built renderer from a real origin (reachout://app) instead of file://.
  protocol.handle(APP_SCHEME, async (request) => {
    const url = new URL(request.url)
    if (url.host !== 'app') return new Response('Not found', { status: 404 })
    const rel = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname)
    const full = resolve(RENDERER_DIR, '.' + rel)
    if (full !== RENDERER_DIR && !full.startsWith(RENDERER_DIR + sep)) {
      return new Response('Forbidden', { status: 403 })
    }
    try {
      const res = await net.fetch(pathToFileURL(full).toString())
      const headers = new Headers(res.headers)
      headers.set('Content-Security-Policy', csp)
      headers.set('X-Content-Type-Options', 'nosniff')
      return new Response(res.body, { status: res.status, headers })
    } catch {
      return new Response('Not found', { status: 404 })
    }
  })
}

app.on('web-contents-created', (_e, contents) => {
  contents.on('will-navigate', (event, url) => {
    if (!url.startsWith(APP_ORIGIN)) event.preventDefault()
  })
  contents.on('will-attach-webview', (event) => event.preventDefault())
  contents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
})

// -------------------------------------------------------------------- IPC

function registerIpc(): void {
  ipcMain.handle(CH.getBackend, (e) => {
    if (!isOurWindow(e, true)) throw new Error('Not allowed')
    return supervisor.info
  })
  ipcMain.handle(CH.getBackendStatus, (e) => {
    if (!isOurWindow(e)) throw new Error('Not allowed')
    return supervisor.currentStatus
  })
  ipcMain.handle(CH.openExternal, async (e, url: unknown) => {
    if (!isOurWindow(e)) throw new Error('Not allowed')
    if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) throw new Error('Only http(s) links can be opened')
    await shell.openExternal(url)
  })
  ipcMain.handle(CH.openLogs, async (e) => {
    if (!isOurWindow(e)) throw new Error('Not allowed')
    await shell.openPath(logsDir())
  })
  ipcMain.handle(CH.openDataFolder, async (e) => {
    if (!isOurWindow(e, true)) throw new Error('Not allowed')
    await shell.openPath(dataDir())
  })
  ipcMain.handle(CH.getAppInfo, (e): AppInfo => {
    if (!isOurWindow(e, true)) throw new Error('Not allowed')
    return {
      version: app.getVersion(), electron: process.versions.electron, platform: process.platform,
      dataDir: dataDir(), logsDir: logsDir(),
    }
  })
  ipcMain.handle(CH.setTheme, (e, theme: unknown) => {
    if (!isOurWindow(e, true)) throw new Error('Not allowed')
    if (theme !== 'system' && theme !== 'light' && theme !== 'dark') throw new Error('Invalid theme')
    nativeTheme.themeSource = theme satisfies ThemePreference
  })
  ipcMain.handle(CH.retryBackend, async (e) => {
    if (!isOurWindow(e)) throw new Error('Not allowed')
    await boot()
  })
  ipcMain.handle(CH.pickPdf, async (e): Promise<string | null> => {
    if (!isOurWindow(e, true) || !mainWin) throw new Error('Not allowed')
    const r = await dialog.showOpenDialog(mainWin, { properties: ['openFile'], filters: [{ name: 'PDF', extensions: ['pdf'] }] })
    return r.canceled ? null : (r.filePaths[0] ?? null)
  })
  ipcMain.handle(CH.quit, (e) => {
    if (!isOurWindow(e)) throw new Error('Not allowed')
    app.quit()
  })
}

// ------------------------------------------------------------------- boot

async function boot(): Promise<void> {
  splashWin ??= createSplash()
  try {
    await supervisor.restart()
  } catch (e) {
    log('error', `backend failed to start: ${e instanceof Error ? e.message : String(e)}`)
    return // the splash shows the error with Try again / View logs / Quit
  }
  mainWin ??= createMainWindow()
}

supervisor.on('status', (status: BackendStatus) => {
  sendToMain(CH.backendStatus, status)
  if (splashWin && !splashWin.isDestroyed()) splashWin.webContents.send(CH.backendStatus, status)
  if (status.state === 'failed' && mainWin) {
    // Crash loop while running: swap the app window for the error screen.
    splashWin ??= createSplash()
    mainWin.close()
  }
})

async function confirmQuitWhileSending(): Promise<boolean> {
  const opts = {
    type: 'question' as const, buttons: ['Pause sending and quit', 'Keep sending'], defaultId: 1, cancelId: 1,
    message: 'A campaign is sending.',
    detail: 'Quitting pauses all sending. You can resume it the next time you open Reachout.',
  }
  // A hidden window cannot host a sheet (the dialog would never appear): use the parent-less variant then.
  const r = mainWin && !mainWin.isDestroyed() && mainWin.isVisible() ? await dialog.showMessageBox(mainWin, opts) : await dialog.showMessageBox(opts)
  return r.response === 0
}

app.on('before-quit', (event) => {
  if (quitting) return
  event.preventDefault()
  void (async () => {
    const snapshot = await monitor.snapshot()
    if (isSending(snapshot)) {
      if (!skipQuitConfirm && !(await confirmQuitWhileSending())) {
        showMainWindow()
        return // the user chose to keep sending
      }
      await monitor.pauseAll() // sending stops cleanly before the processes are killed
    }
    quitting = true
    tray.destroy()
    if (monitorTimer) clearInterval(monitorTimer)
    log('info', 'quitting: stopping backend')
    await supervisor.stop().finally(() => app.exit(0))
  })()
})
app.on('window-all-closed', () => app.quit())
process.on('SIGTERM', () => { skipQuitConfirm = true; app.quit() })
process.on('SIGINT', () => { skipQuitConfirm = true; app.quit() })

void app.whenReady().then(async () => {
  if (!app.hasSingleInstanceLock()) return
  log('info', `starting Reachout ${app.getVersion()} dev=${isDev} origin=${APP_ORIGIN}`)
  hardenSession()
  registerIpc()
  const send = (action: MenuAction): void => sendToMain(CH.menu, action)
  Menu.setApplicationMenu(
    buildMenu({
      isDev, send,
      showLogs: () => void shell.openPath(logsDir()),
      openDataFolder: () => void shell.openPath(dataDir()),
    }),
  )
  splashWin = createSplash()
  await boot()
  monitorTimer = setInterval(() => void refreshSending(), 4000)
})
