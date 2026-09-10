import { readFileSync } from 'fs'
import { dirname, join } from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

// dist/src/utils -> package root. The table is shared with the Python side.
const DATA_FILE = join(__dirname, '..', '..', '..', 'scripts', 'data', 'wechat_emoji.json')

const faces: Record<string, string> = {}
try {
  const parsed = JSON.parse(readFileSync(DATA_FILE, 'utf8')) as Record<string, unknown>
  for (const [name, emoji] of Object.entries(parsed)) {
    // Entries starting with "_" are documentation, not faces.
    if (!name.startsWith('_') && typeof emoji === 'string') {
      faces[name] = emoji
    }
  }
} catch {
  // Missing table just means faces stay as their [名称] source text.
}

const escapeRe = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const names = Object.keys(faces).sort((a, b) => b.length - a.length)
// Longest name first so [左哼哼] can never be shadowed by a shorter prefix.
const pattern = names.length > 0 ? new RegExp(`\\[(${names.map(escapeRe).join('|')})\\]`, 'g') : null

/** Replace known WeChat face codes like [害羞] with emoji; unknown codes pass through. */
export function convertFaces(text: string | undefined | null): string {
  if (!text || !pattern) return text || ''
  return text.replace(pattern, (match, name: string) => faces[name] ?? match)
}

/** Convert faces, then truncate without splitting a surrogate pair. */
export function convertFacesTruncated(text: string | undefined | null, max: number): string {
  return Array.from(convertFaces(text)).slice(0, max).join('')
}
