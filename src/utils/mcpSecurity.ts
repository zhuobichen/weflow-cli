import { readFileSync, statSync } from 'fs'
import { isAbsolute, relative, resolve } from 'path'

export function safeChildPath(root: string, child: string): string | null {
  const candidate = resolve(root, child)
  const rel = relative(resolve(root), candidate)
  return rel && !rel.startsWith('..') && !isAbsolute(rel) ? candidate : null
}

export function safeDate(value: unknown): string | null {
  const date = String(value || '')
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : null
}

export function isCoverImage(path: string): boolean {
  try {
    const stat = statSync(path)
    if (!stat.isFile() || stat.size > 10 * 1024 * 1024) return false
    const head = readFileSync(path).subarray(0, 12)
    return (head.length >= 8 && head.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) ||
      (head.length >= 3 && head.subarray(0, 3).equals(Buffer.from([255, 216, 255]))) ||
      (head.length >= 6 && (head.subarray(0, 6).toString() === 'GIF87a' || head.subarray(0, 6).toString() === 'GIF89a')) ||
      (head.length >= 12 && head.subarray(0, 4).toString() === 'RIFF' && head.subarray(8, 12).toString() === 'WEBP')
  } catch {
    return false
  }
}
