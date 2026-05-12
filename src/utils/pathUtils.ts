import { homedir } from 'os'

/**
 * Expand "~" prefix to current user's home directory.
 */
export function expandHomePath(inputPath: string): string {
  const raw = String(inputPath || '').trim()
  if (!raw) return raw

  if (raw === '~') return homedir()
  if (/^~[\\/]/.test(raw)) {
    return `${homedir()}${raw.slice(1)}`
  }

  return raw
}
