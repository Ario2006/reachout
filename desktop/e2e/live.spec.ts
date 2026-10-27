import { execFileSync } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { _electron as electron, expect, test, type Page } from '@playwright/test'

const SHOTS = process.env['SHOT_DIR'] ?? 'test-results/shots'
const root = resolve(process.cwd(), '..')

// Opt-in: needs the internet and a running Ollama with the model pulled.   REACHOUT_LIVE=1 npm run test:e2e
test.skip(!process.env['REACHOUT_LIVE'], 'set REACHOUT_LIVE=1 to run against the real web and model')

test('REAL end to end: plan with the local model, run a real search in the app, watch it stream', async () => {
  test.setTimeout(420_000)
  const home = mkdtempSync(join(tmpdir(), 'reachout-live-'))
  const env = { ...process.env, REACHOUT_HOME: home, PYTHONPATH: root } as Record<string, string>
  execFileSync(join(root, '.venv/bin/python'), ['-c',
    'from backend.core import config, db, models; config.ensure_dirs(); e=db.get_engine(); db.migrate(e);' +
    'db.save_settings(models.AppSettings(first_run_complete=True, legal_acknowledged=True, max_domains_per_run=8, target_leads=0), e)'], { env })

  const app = await electron.launch({ args: ['.'], cwd: process.cwd(), env })
  const isApp = (p: Page) => p.url().startsWith('reachout://app')
  const page = app.windows().find(isApp) ?? await app.waitForEvent('window', { predicate: isApp, timeout: 60_000 })
  await page.setViewportSize({ width: 1340, height: 900 })
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))

  await page.getByLabel('Who do you want to reach?').fill('Founders of D2C brands who need a website redesign')
  const t0 = Date.now()
  await page.getByRole('button', { name: 'Make a plan' }).click()
  const card = page.getByTestId('plan-card')
  await expect(card).toBeVisible({ timeout: 90_000 })
  console.log(`plan ready in ${((Date.now() - t0) / 1000).toFixed(1)}s:`, await card.getByText(/Written by|Template/).first().textContent())
  await page.screenshot({ path: `${SHOTS}/live-plan.png` })

  await card.getByRole('button', { name: 'Run search' }).click()
  const progress = page.getByTestId('run-progress')
  await expect(progress).toBeVisible()
  await expect(page.getByTestId('run-log')).toContainText('Searched', { timeout: 120_000 })
  await page.screenshot({ path: `${SHOTS}/live-searching.png` })

  // The counters and the sites table must change while the run is still going (live events, not a final dump).
  await expect(page.locator('[data-testid^="domain-"]').first()).toBeVisible({ timeout: 180_000 })
  const seen = new Set<string>()
  const deadline = Date.now() + 300_000
  while (Date.now() < deadline) {
    seen.add((await page.getByTestId('lead-counters').textContent()) ?? '')
    if (await page.getByTestId('run-summary').isVisible()) break
    await page.waitForTimeout(1500)
  }
  console.log('distinct lead-counter values seen while running:', [...seen].map((s) => s.slice(0, 40)))
  await page.screenshot({ path: `${SHOTS}/live-running.png` })
  await expect(page.getByTestId('run-summary')).toBeVisible({ timeout: 300_000 })
  console.log('summary:', await page.getByTestId('run-summary').textContent())
  console.log('sites listed:', await page.locator('[data-testid^="domain-"]').count())

  await page.getByRole('link', { name: /Review \d+ new leads/ }).click()
  await expect(page.getByRole('heading', { name: 'Leads' })).toBeVisible()
  await expect(page.getByTestId('lead-row').first()).toBeVisible({ timeout: 20_000 })
  console.log('leads page:', await page.getByTestId('lead-count').textContent())
  await page.waitForFunction(() => document.getAnimations().length === 0)
  await page.screenshot({ path: `${SHOTS}/live-leads.png` })
  await page.getByTestId('lead-row').first().getByRole('button', { name: /^Name:/ }).click()
  await expect(page.getByTestId('lead-drawer')).toBeVisible()
  console.log('drawer source:', (await page.getByRole('region', { name: 'Source' }).textContent())?.slice(0, 200))
  expect(errors).toEqual([])
  await app.close()
})
