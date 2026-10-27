import { type ChildProcess, spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { closeSync, mkdirSync, openSync } from 'node:fs'
import { createServer } from 'node:net'
import { join } from 'node:path'
import type { BackendInfo, BackendStatus } from '../shared/bridge'
import { log } from './logger'

const MAX_RESTARTS = 3
const RESTART_WINDOW_MS = 60_000
const HEALTH_TIMEOUT_MS = 40_000
const KILL_GRACE_MS = 30_000  // must exceed backend/worker.py SEND_DRAIN_S (25 s)

interface Options {
  python: string
  repoRoot: string
  logsDir: string
  /** The renderer origin the API will accept (CORS + Origin check). */
  allowedOrigin: string
  /** Pass --reload to uvicorn (dev only). */
  reload: boolean
}

type Role = 'api' | 'worker'

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer()
    srv.once('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address()
      if (addr && typeof addr === 'object') {
        const { port } = addr
        srv.close(() => resolve(port))
      } else {
        srv.close(() => reject(new Error('Could not determine a free port')))
      }
    })
  })
}

export class BackendSupervisor extends EventEmitter {
  private readonly token = randomBytes(32).toString('hex')
  private port = 0
  private children = new Map<Role, ChildProcess>()
  private crashTimes: number[] = []
  private stopping = false
  private status: BackendStatus = { state: 'starting', message: 'Starting…' }

  constructor(private readonly opts: Options) {
    super()
  }

  get info(): BackendInfo {
    return { baseUrl: `http://127.0.0.1:${this.port}`, token: this.token }
  }

  get currentStatus(): BackendStatus {
    return this.status
  }

  private setStatus(state: BackendStatus['state'], message: string): void {
    this.status = { state, message }
    log('info', `backend ${state}: ${message}`)
    this.emit('status', this.status)
  }

  /** Spawn the API and worker and wait until /health answers. Throws (after cleanup) on failure. */
  async start(): Promise<void> {
    this.stopping = false
    this.crashTimes = []
    try {
      if (!this.port) this.port = await freePort()
      mkdirSync(this.opts.logsDir, { recursive: true })

      this.setStatus('starting', 'Starting backend…')
      this.spawnRole('api')
      this.setStatus('starting', 'Starting worker…')
      this.spawnRole('worker')

      this.setStatus('starting', 'Waiting for backend…')
      await this.waitForHealth()
      this.setStatus('starting', 'Checking Ollama…')
      await this.authedFetch('/setup/status').catch(() => undefined) // warms the checks; result unused
      this.setStatus('ready', 'Ready')
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      this.stopping = true
      await this.killAll()
      this.setStatus('failed', message)
      throw e
    }
  }

  private args(role: Role): string[] {
    if (role === 'worker') return ['-m', 'backend.worker']
    const a = [
      '-m', 'uvicorn', 'backend.api.main:create_app', '--factory',
      '--host', '127.0.0.1', '--port', String(this.port), '--log-level', 'info',
    ]
    if (this.opts.reload) a.push('--reload', '--reload-dir', join(this.opts.repoRoot, 'backend'))
    return a
  }

  private spawnRole(role: Role): void {
    const fd = openSync(join(this.opts.logsDir, `${role}.log`), 'a')
    const child = spawn(this.opts.python, this.args(role), {
      cwd: this.opts.repoRoot,
      stdio: ['ignore', fd, fd],
      env: {
        ...process.env,
        PYTHONPATH: this.opts.repoRoot,
        PYTHONUNBUFFERED: '1',
        REACHOUT_API_TOKEN: this.token,
        REACHOUT_ALLOWED_ORIGINS: this.opts.allowedOrigin,
        REACHOUT_WATCH_PARENT: '1', // children exit if this process dies
      },
    })
    closeSync(fd) // the child holds its own copy
    this.children.set(role, child)
    child.once('error', (e) => log('error', `${role} failed to spawn: ${e.message}`))
    child.once('exit', (code, signal) => this.onExit(role, child, code, signal))
    log('info', `${role} started pid=${child.pid}`)
  }

  private onExit(role: Role, child: ChildProcess, code: number | null, signal: string | null): void {
    if (this.children.get(role) === child) this.children.delete(role)
    if (this.stopping) return
    log('warn', `${role} exited code=${code} signal=${signal}`)

    const now = Date.now()
    this.crashTimes = this.crashTimes.filter((t) => now - t < RESTART_WINDOW_MS)
    this.crashTimes.push(now)
    if (this.crashTimes.length > MAX_RESTARTS) {
      this.setStatus('failed', `The ${role} keeps crashing (${MAX_RESTARTS} restarts in a minute).`)
      void this.killAll()
      return
    }
    this.setStatus('restarting', `Restarting ${role}…`)
    setTimeout(() => {
      if (this.stopping) return
      this.spawnRole(role)
      if (role === 'api') {
        this.waitForHealth()
          .then(() => this.setStatus('ready', 'Ready'))
          .catch((e: Error) => this.setStatus('failed', e.message))
      } else {
        this.setStatus('ready', 'Ready')
      }
    }, 500 * this.crashTimes.length)
  }

  private authedFetch(path: string): Promise<Response> {
    return fetch(this.info.baseUrl + path, {
      headers: { Authorization: `Bearer ${this.token}` },
      signal: AbortSignal.timeout(5000),
    })
  }

  private async waitForHealth(): Promise<void> {
    const deadline = Date.now() + HEALTH_TIMEOUT_MS
    while (Date.now() < deadline) {
      if (!this.children.has('api') && !this.stopping) {
        throw new Error('The backend exited during startup. See the API log for details.')
      }
      try {
        const r = await this.authedFetch('/health')
        if (r.ok) return
      } catch {
        // not listening yet
      }
      await new Promise((r) => setTimeout(r, 250))
    }
    throw new Error('The backend did not become healthy in time.')
  }

  /** Stop both processes: SIGTERM, then SIGKILL after 30 s (longer than the worker's 25 s drain of an in-flight send). */
  async stop(): Promise<void> {
    this.stopping = true
    await this.killAll()
  }

  /** Used by the error screen: try again from scratch. */
  async restart(): Promise<void> {
    await this.stop()
    await this.start()
  }

  private async killAll(): Promise<void> {
    const procs = [...this.children.values()]
    this.children.clear()
    await Promise.all(procs.map((p) => this.terminate(p)))
  }

  private terminate(p: ChildProcess): Promise<void> {
    return new Promise((resolve) => {
      if (p.exitCode !== null || p.signalCode !== null) return resolve()
      const timer = setTimeout(() => p.kill('SIGKILL'), KILL_GRACE_MS)
      p.once('exit', () => {
        clearTimeout(timer)
        resolve()
      })
      p.kill('SIGTERM')
    })
  }
}
