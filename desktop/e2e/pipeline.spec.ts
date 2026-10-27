import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test'

const SHOTS = process.env['SHOT_DIR'] ?? 'test-results/shots'
const root = resolve(process.cwd(), '..')
const LEADS = 5003

test.describe.configure({ mode: 'serial' })

test.describe('discover and leads (seeded data, Ollama unreachable)', () => {
  let app: ElectronApplication
  let page: Page

  test.beforeAll(async () => {
    const home = mkdtempSync(join(tmpdir(), 'reachout-pipe-'))
    const env = {
      ...process.env, REACHOUT_HOME: home, PYTHONPATH: root,
      OLLAMA_HOST: 'http://127.0.0.1:9', // nothing listens: the planner must fall back to its template plan
    } as Record<string, string>
    execFileSync(join(root, '.venv/bin/python'), [join(process.cwd(), 'e2e/seed.py'), '5000'], { env })
    app = await electron.launch({ args: ['.'], cwd: process.cwd(), env })
    const isApp = (p: Page) => p.url().startsWith('reachout://app')
    page = app.windows().find(isApp) ?? await app.waitForEvent('window', { predicate: isApp, timeout: 60_000 })
    await page.setViewportSize({ width: 1340, height: 860 })
  })
  test.afterAll(async () => { await app.close() })

  test('opens on Discover and makes a template plan when the model is unreachable', async () => {
    await expect(page.getByRole('heading', { name: 'Discover' })).toBeVisible()
    await page.getByLabel('Who do you want to reach?').fill('Pre-seed investors who back AI developer tools in India/SEA')
    await page.getByRole('button', { name: 'Make a plan' }).click()
    const card = page.getByTestId('plan-card')
    await expect(card).toBeVisible({ timeout: 20_000 })
    await expect(card.getByText('Template plan')).toBeVisible()
    await expect(card.getByRole('button', { name: 'Investor', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect(card.getByRole('list', { name: 'Regions' })).toContainText('India')
    const queries = card.getByRole('list', { name: 'Search queries' }).getByRole('listitem')
    expect(await queries.count()).toBeGreaterThanOrEqual(6)
    await card.getByRole('button', { name: /^Remove / }).first().click()
    await card.getByRole('textbox', { name: /Industries/ }).fill('devtools')
    await card.getByRole('textbox', { name: /Industries/ }).press('Enter')
    await expect(card.getByRole('list', { name: 'Industries' })).toContainText('devtools')
    await page.waitForFunction(() => document.getAnimations().length === 0)
    await page.screenshot({ path: `${SHOTS}/discover-plan.png` })
  })

  test('a finished run shows its summary, log and sites, and links to its leads', async () => {
    await page.getByRole('region', { name: 'Past searches' }).getByRole('button', { name: /AI\/ML internships/ }).click()
    const sum = page.getByTestId('run-summary')
    await expect(sum).toContainText('5,003 new leads from 3 sites')
    await expect(page.getByTestId('run-log')).toContainText('Searched “ai startup bangalore careers”')
    await expect(page.getByTestId('domain-slowsite.io')).toContainText('Failed')
    await expect(page.getByTestId('domain-acme.ai')).toContainText('Done')
    await page.waitForFunction(() => document.getAnimations().length === 0)
    await page.screenshot({ path: `${SHOTS}/discover-run.png` })
    await sum.getByRole('link', { name: /Review 5003 new leads/ }).click()
    await expect(page.getByRole('heading', { name: 'Leads' })).toBeVisible()
    await expect(page.getByTestId('lead-count')).toContainText('5,003 matching')
    expect(page.url()).toContain('/leads?run=1')
  })

  test('5,003 leads render as a window of rows and scroll smoothly', async () => {
    await page.getByRole('button', { name: 'Clear filters' }).click()
    await expect(page.getByTestId('lead-count')).toContainText(`${LEADS.toLocaleString()} leads`)
    const rows = page.getByTestId('lead-row')
    await expect(rows.first()).toBeVisible()
    expect(await rows.count()).toBeLessThan(60)
    // every default column fits in the window: no sideways scrolling to find Status or Last activity
    for (const name of [/Status/, /Last activity/]) {
      const box = await page.getByRole('columnheader', { name }).boundingBox()
      expect(box && box.x + box.width).toBeLessThanOrEqual(1340)
    }

    const frames = await page.evaluate(async () => {
      const el = document.querySelector<HTMLElement>('[data-testid="leads-scroller"]')
      if (!el) throw new Error('no scroller')
      const deltas: number[] = []
      let last = performance.now()
      const steps = 120
      for (let i = 1; i <= steps; i++) {
        el.scrollTop = (el.scrollHeight - el.clientHeight) * (i / steps)
        await new Promise<void>((r) => requestAnimationFrame(() => r()))
        const now = performance.now()
        deltas.push(now - last)
        last = now
      }
      deltas.sort((a, b) => a - b)
      return { p50: deltas[Math.floor(deltas.length * 0.5)] ?? 0, p95: deltas[Math.floor(deltas.length * 0.95)] ?? 0, max: deltas[deltas.length - 1] ?? 0, rendered: document.querySelectorAll('[data-testid="lead-row"]').length }
    })
    test.info().annotations.push({ type: 'scroll-frames-ms', description: JSON.stringify(frames) })
    console.log('scroll frame times (ms):', JSON.stringify(frames))
    expect(frames.rendered).toBeLessThan(60)
    expect(frames.p95).toBeLessThan(34) // ~30fps worst case for 95% of frames
    await page.evaluate(() => { const el = document.querySelector<HTMLElement>('[data-testid="leads-scroller"]'); if (el) el.scrollTop = 0 })
  })

  test('search, filter, sort', async () => {
    await page.getByLabel('Search leads').fill('priya@acme')
    await expect(page.getByTestId('lead-count')).toContainText('1 matching')
    await expect(page.getByTestId('lead-row')).toHaveCount(1)
    await page.getByLabel('Search leads').fill('')
    await page.getByRole('button', { name: 'Filter by email type' }).click()
    await page.getByRole('menuitemcheckbox', { name: 'Guessed' }).click()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('lead-count')).toContainText('matching')
    await expect(page.getByTestId('lead-row').first().getByTestId('conf-guessed')).toBeVisible()
    await page.getByRole('button', { name: 'Clear filters' }).click()

    await page.getByRole('columnheader', { name: /Company/ }).getByRole('button').click()
    await expect(page.getByRole('columnheader', { name: /Company/ })).toHaveAttribute('aria-sort', 'ascending')
    await page.keyboard.down('Shift')
    await page.getByRole('columnheader', { name: /Name/ }).getByRole('button').click()
    await page.keyboard.up('Shift')
    await expect(page.getByRole('columnheader', { name: /Name/ })).toHaveAttribute('aria-sort', 'ascending')
    expect(page.url()).toContain('sort=company%3Aasc%2Cname%3Aasc')
    await page.getByRole('columnheader', { name: /Company/ }).getByRole('button').click()
    await page.getByRole('columnheader', { name: /Company/ }).getByRole('button').click()
    await expect(page.getByRole('columnheader', { name: /Company/ })).toHaveAttribute('aria-sort', 'none')
  })

  test('drawer shows the source and context; edits persist', async () => {
    await page.getByLabel('Search leads').fill('priya@acme')
    await expect(page.getByTestId('lead-row')).toHaveCount(1) // wait for the debounced search to apply
    await page.getByTestId('lead-row').getByRole('button', { name: /^Name:/ }).click() // clicking the name opens it
    const drawer = page.getByTestId('lead-drawer')
    await expect(drawer).toBeVisible()
    await expect(drawer.getByText('“Priya Sharma, Co-founder & CEO. Email Priya to talk about internships.”')).toBeVisible()
    await expect(drawer.getByRole('link', { name: /acme\.ai\/team/ })).toBeVisible()
    await drawer.getByRole('textbox', { name: 'Notes' }).fill('Met at demo day')
    await drawer.getByRole('combobox').click()
    await page.getByRole('option', { name: 'Closed' }).click()
    await drawer.getByRole('button', { name: 'Save changes' }).click()
    await expect(page.getByText('Saved', { exact: true })).toBeVisible()
    await page.waitForFunction(() => document.getAnimations().length === 0)
    await page.screenshot({ path: `${SHOTS}/lead-drawer.png` })
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('lead-row').getByText('Closed')).toBeVisible()
  })

  test('inline edit by double-click saves without opening the drawer', async () => {
    await page.getByRole('button', { name: /^Company: Acme AI/ }).first().dblclick()
    const input = page.getByRole('textbox', { name: 'Edit Company' })
    await input.fill('Acme Labs')
    await input.press('Enter')
    await expect(page.getByRole('button', { name: /^Company: Acme Labs/ })).toBeVisible()
    await page.waitForTimeout(500) // longer than the single-click delay: the drawer must stay shut
    await expect(page.getByTestId('lead-drawer')).toHaveCount(0)
  })

  test('bulk add to suppression closes the leads and lists them in Settings', async () => {
    await page.getByLabel('Search leads').fill('acme.ai')
    await expect(page.getByTestId('lead-count')).toContainText('3 matching')
    await page.getByRole('checkbox', { name: 'Select all loaded leads' }).click()
    const bar = page.getByTestId('bulk-bar')
    await expect(bar).toContainText('3 selected')
    await bar.getByRole('button', { name: /Add to suppression/ }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Add to suppression' }).click()
    await expect(page.getByText('3 addresses suppressed')).toBeVisible()
    await page.getByLabel('Search leads').fill('')
    await page.getByRole('link', { name: 'Settings' }).click()
    await page.getByRole('tab', { name: 'Suppression' }).click()
    await expect(page.getByRole('cell', { name: 'priya@acme.ai', exact: true })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'careers@acme.ai', exact: true })).toBeVisible()
  })

  test('export downloads a CSV with the current filters and safe cells', async () => {
    await page.getByRole('link', { name: 'Leads' }).click()
    await page.getByLabel('Search leads').fill('acme.ai')
    await expect(page.getByTestId('lead-count')).toContainText('3 matching')
    // Electron asks for a save location with a native dialog; answer it from the main process.
    const dir = mkdtempSync(join(tmpdir(), 'reachout-dl-'))
    await app.evaluate(({ session }, d) => {
      session.defaultSession.once('will-download', (_e, item) => item.setSavePath(`${d}/${item.getFilename()}`))
    }, dir)
    await page.getByRole('button', { name: /Export/ }).click()
    const file = join(dir, 'leads.csv')
    await expect.poll(() => existsSync(file), { timeout: 15_000 }).toBe(true)
    await expect.poll(() => readFileSync(file, 'utf8').length, { timeout: 5_000 }).toBeGreaterThan(50)
    const csv = readFileSync(file, 'utf8')
    expect(csv.split('\n')[0]).toBe('name,email,role,company,domain,source_url,email_confidence,mode,notes')
    expect(csv).toContain('priya@acme.ai')
    expect(csv).not.toContain('company0')
  })

  test('the command palette finds a lead by name and opens it filtered', async () => {
    await page.getByLabel('Search leads').fill('')
    await page.keyboard.press('Meta+k')
    await page.getByPlaceholder(/Type a command/).fill('priya')
    await page.getByRole('option', { name: /Priya Sharma/ }).click()
    await expect(page.getByLabel('Search leads')).toHaveValue('priya@acme.ai')
    await expect(page.getByTestId('lead-row')).toHaveCount(1)
    await page.waitForFunction(() => document.getAnimations().length === 0)
    await page.getByLabel('Search leads').fill('')
    await page.screenshot({ path: `${SHOTS}/leads-light.png` })
  })

  test('leads table in dark mode', async () => {
    await page.getByRole('link', { name: 'Settings' }).click()
    await page.getByRole('tab', { name: 'About' }).click()
    await page.getByRole('button', { name: 'Dark' }).click()
    await page.getByRole('link', { name: 'Leads' }).click()
    await expect(page.getByTestId('lead-row').first()).toBeVisible()
    await page.mouse.move(5, 5)
    await page.waitForFunction(() => document.getAnimations().length === 0)
    await page.screenshot({ path: `${SHOTS}/leads-dark.png` })
  })
})
