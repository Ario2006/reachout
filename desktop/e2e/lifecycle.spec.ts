import { execFileSync } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test'

type Role = 'api' | 'worker'

function childPids(parent: number): Partial<Record<Role, number>> {
  const out = execFileSync('ps', ['-axo', 'pid,ppid,command']).toString()
  const found: Partial<Record<Role, number>> = {}
  for (const line of out.split('\n')) {
    const m = /^\s*(\d+)\s+(\d+)\s+(.*)$/.exec(line)
    if (!m || Number(m[2]) !== parent) continue
    const cmd = m[3] ?? ''
    if (cmd.includes('uvicorn')) found.api = Number(m[1])
    else if (cmd.includes('backend.worker')) found.worker = Number(m[1])
  }
  return found
}

const alive = (pid: number): boolean => {
  try { process.kill(pid, 0); return true } catch { return false }
}

async function launch(): Promise<ElectronApplication> {
  return electron.launch({
    args: ['.'],
    cwd: process.cwd(),
    env: { ...process.env, REACHOUT_HOME: mkdtempSync(join(tmpdir(), 'reachout-e2e-')), REACHOUT_SKIP_WIZARD: '1' } as Record<string, string>,
  })
}

const isApp = (p: Page): boolean => p.url().startsWith('reachout://app')
const appWindow = async (app: ElectronApplication): Promise<Page> =>
  app.windows().find(isApp) ?? app.waitForEvent('window', { predicate: isApp, timeout: 60_000 })

test('quitting stops both backend processes', async () => {
  const app = await launch()
  await appWindow(app)
  const main = app.process().pid
  if (!main) throw new Error('no electron pid')
  const { api, worker } = childPids(main)
  expect(api).toBeTruthy()
  expect(worker).toBeTruthy()

  await app.close()
  await expect.poll(() => [api, worker].some((p) => p !== undefined && alive(p)), { timeout: 10_000 }).toBe(false)
})

test('a crash-looping backend shows the error screen, and Try again recovers', async () => {
  const app = await launch()
  await appWindow(app)
  const main = app.process().pid
  if (!main) throw new Error('no electron pid')

  // The supervisor tolerates 3 restarts per minute; the 4th crash is fatal.
  let last = childPids(main).api
  for (let i = 0; i < 4; i += 1) {
    if (last === undefined) throw new Error('api process missing')
    process.kill(last, 'SIGKILL')
    if (i < 3) {
      await expect.poll(() => { const p = childPids(main).api; return p !== undefined && p !== last }, { timeout: 15_000 }).toBe(true)
      last = childPids(main).api
    }
  }

  const errorScreen = await app.waitForEvent('window', { predicate: (p) => !isApp(p), timeout: 15_000 })
    .catch(() => app.windows().find((p) => !isApp(p)))
  if (!errorScreen) throw new Error('error screen did not appear')
  await expect(errorScreen.getByText(/keeps crashing/)).toBeVisible()
  await expect(errorScreen.getByRole('button', { name: 'View logs' })).toBeVisible()
  await expect(errorScreen.getByRole('button', { name: 'Quit' })).toBeVisible()
  expect(childPids(main).api).toBeUndefined() // nothing left running while failed

  await errorScreen.getByRole('button', { name: 'Try again' }).click()
  const page = await appWindow(app)
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 30_000 })
  await app.close()
})
