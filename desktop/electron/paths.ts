import { homedir } from 'node:os'
import { join, resolve } from 'node:path'
import { app } from 'electron'

/** Same rule as backend/core/config.py: $REACHOUT_HOME or ~/Reachout. */
export const dataDir = (): string => process.env['REACHOUT_HOME'] || join(homedir(), 'Reachout')
export const logsDir = (): string => join(dataDir(), 'logs')

/** Repository root (parent of desktop/) when running from source. */
export const repoRoot = (): string => resolve(app.getAppPath(), '..')

export const pythonPath = (): string =>
  process.env['REACHOUT_PYTHON'] || join(repoRoot(), '.venv', 'bin', 'python')

/** Packaged app: the PyInstaller bundle shipped as an extraResource (see package.json "build"). */
export const backendBinary = (): string =>
  process.env['REACHOUT_BACKEND_BIN'] || join(process.resourcesPath, 'backend', 'reachout-backend', 'reachout-backend')

/** Packaged builds run the bundled backend; dev runs python from the repo .venv. */
export const useBundledBackend = (): boolean => app.isPackaged
