import { appendFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { logsDir } from './paths'

export function log(level: 'info' | 'warn' | 'error', message: string): void {
  const line = `${new Date().toISOString()} ${level.toUpperCase()} ${message}\n`
  try {
    mkdirSync(logsDir(), { recursive: true })
    appendFileSync(join(logsDir(), 'desktop.log'), line)
  } catch {
    // Logging must never crash the app.
  }
  if (level === 'error') console.error(line.trimEnd())
}
