import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: 'e2e',
  timeout: 90_000,
  workers: 1, // one Electron app at a time (single-instance lock)
  reporter: 'list',
  outputDir: 'test-results',
})
