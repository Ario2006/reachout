import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { ReachoutBridge } from '../shared/bridge'
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test'

const SHOTS = process.env['SHOT_DIR'] ?? 'test-results/shots'

async function launch(extraEnv: Record<string, string> = {}): Promise<{ app: ElectronApplication; home: string }> {
  const home = mkdtempSync(join(tmpdir(), 'reachout-e2e-'))
  const app = await electron.launch({
    args: ['.'],
    cwd: process.cwd(),
    env: { ...process.env, REACHOUT_HOME: home, ...extraEnv } as Record<string, string>,
  })
  return { app, home }
}

/** The splash window opens first; wait for the real app window. */
async function appWindow(app: ElectronApplication): Promise<Page> {
  const isApp = (p: Page) => p.url().startsWith('reachout://app')
  const existing = app.windows().find(isApp)
  if (existing) return existing
  return app.waitForEvent('window', { predicate: isApp, timeout: 60_000 })
}

test.describe('app shell (wizard skipped)', () => {
  let app: ElectronApplication
  let page: Page

  test.beforeAll(async () => {
    ;({ app } = await launch({ REACHOUT_SKIP_WIZARD: '1' }))
    page = await appWindow(app)
    await page.setViewportSize({ width: 1240, height: 820 })
  })
  test.afterAll(async () => { await app.close() })

  test('sidebar shows 7 screens; only Settings is enabled', async () => {
    const nav = page.getByRole('navigation', { name: 'Main' })
    await expect(nav.getByRole('link')).toHaveCount(7)
    await expect(nav.locator('[aria-disabled="true"]')).toHaveCount(6)
    await expect(nav.getByRole('link', { name: 'Settings' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
    await page.screenshot({ path: `${SHOTS}/settings-account-light.png` })
  })

  test('renderer is sandboxed and the API demands a token', async () => {
    const result = await page.evaluate(async () => {
      const bridge = (window as unknown as { reachout?: ReachoutBridge }).reachout
      if (!bridge) throw new Error('bridge missing')
      const { baseUrl, token } = await bridge.getBackend()
      const noToken = await fetch(`${baseUrl}/health`)
      const withToken = await fetch(`${baseUrl}/health`, { headers: { Authorization: `Bearer ${token}` } })
      return {
        hasRequire: typeof (window as unknown as { require?: unknown }).require !== 'undefined',
        hasProcess: typeof (window as unknown as { process?: unknown }).process !== 'undefined',
        noToken: noToken.status,
        withToken: withToken.status,
        bridgeKeys: Object.keys(bridge).sort(),
      }
    })
    expect(result.hasRequire).toBe(false)
    expect(result.hasProcess).toBe(false)
    expect(result.noToken).toBe(401)
    expect(result.withToken).toBe(200)
    expect(result.bridgeKeys).not.toContain('ipcRenderer')
  })

  test('changing the daily cap saves and warns above the safe default', async () => {
    await page.getByRole('tab', { name: 'Sending' }).click()
    const cap = page.getByLabel('Daily cap')
    await cap.fill('30')
    await expect(page.getByText('Above the safe default of 25')).toBeVisible()
    await page.getByRole('button', { name: 'Save changes' }).click()
    await expect(page.getByText('Settings saved')).toBeVisible()
    await expect(page.getByText(/above the safe default of 25\. New or cold Gmail/i)).toBeVisible()
    await page.waitForFunction(() => document.getAnimations().length === 0)
    await page.screenshot({ path: `${SHOTS}/settings-sending-light.png` })
  })

  test('invalid values are blocked with inline errors', async () => {
    await page.getByLabel('Daily cap').fill('500')
    await page.getByRole('button', { name: 'Save changes' }).click()
    await expect(page.getByText('At most 100')).toBeVisible()
  })

  test('command palette opens with Cmd+K and navigates', async () => {
    await page.keyboard.press('Meta+k')
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await page.getByPlaceholder('Type a command…').fill('suppression')
    await page.getByRole('option', { name: /Settings: Suppression/ }).click()
    await expect(page.getByRole('tab', { name: 'Suppression', selected: true })).toBeVisible()
    await expect(page.getByText('Nobody is suppressed yet')).toBeVisible()
  })

  test('suppression: add, then remove needs the typed address', async () => {
    await page.getByLabel('Email to suppress').fill('Nope@Example.com')
    await page.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(page.getByRole('cell', { name: 'nope@example.com', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Remove nope@example.com' }).click()
    const confirm = page.getByRole('button', { name: 'Remove', exact: true })
    await expect(confirm).toBeDisabled()
    await page.getByLabel('Type the email to confirm').fill('nope@example.com')
    await expect(confirm).toBeEnabled()
    await confirm.click()
    await expect(page.getByText('Nobody is suppressed yet')).toBeVisible()
  })

  test('dark theme', async () => {
    await page.getByRole('tab', { name: 'About' }).click()
    await page.getByRole('button', { name: 'Dark' }).click()
    await expect(page.locator('html')).toHaveClass(/dark/)
    await page.mouse.move(5, 5)
    await page.waitForFunction(() => document.getAnimations().length === 0)
    await page.screenshot({ path: `${SHOTS}/settings-about-dark.png` })
    await page.getByRole('button', { name: 'System' }).click()
  })
})

test('first launch without the skip flag shows the setup wizard', async () => {
  const { app } = await launch()
  try {
    const page = await appWindow(app)
    await page.setViewportSize({ width: 1240, height: 820 })
    await expect(page.getByRole('heading', { name: 'Welcome to Reachout' })).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0)
    await page.screenshot({ path: `${SHOTS}/wizard-light.png` })
  } finally {
    await app.close()
  }
})
