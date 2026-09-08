import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export function resolvePackageRoot(moduleUrl: string): string {
  let current = dirname(fileURLToPath(moduleUrl))
  for (let depth = 0; depth < 6; depth++) {
    if (existsSync(join(current, 'package.json')) && existsSync(join(current, 'scripts'))) return current
    const parent = dirname(current)
    if (parent === current) break
    current = parent
  }
  throw new Error('Unable to locate the WeFlow CLI package root')
}
