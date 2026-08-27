import test from 'node:test'
import assert from 'node:assert/strict'
import { homedir } from 'node:os'
import { expandHomePath } from '../src/utils/pathUtils.js'
import { maskMessageBodyText, redactText } from '../src/services/assistantPrivacy.js'
import { isCoverImage, safeChildPath, safeDate } from '../src/utils/mcpSecurity.js'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

test('expands home directory prefixes without changing other paths', () => {
  assert.equal(expandHomePath('~'), homedir())
  assert.equal(expandHomePath('~/data'), `${homedir()}/data`)
  assert.equal(expandHomePath('C:\\data'), 'C:\\data')
  assert.equal(expandHomePath(''), '')
})

test('redacts common outbound PII in balanced mode', () => {
  const result = redactText(
    '电话 13812345678，邮箱 user@example.com，链接 https://example.com/a 密钥 sk-abcdefghijklmnop',
    'balanced',
    false,
  )

  assert.equal(result.redactions, 4)
  assert.equal(result.safe, '电话 [电话]，邮箱 [邮箱]，链接 [链接] 密钥 [密钥]')
})

test('does not redact open or local inference input', () => {
  const text = '13812345678 user@example.com'
  assert.deepEqual(redactText(text, 'open', false), { safe: text, redactions: 0 })
  assert.deepEqual(redactText(text, 'balanced', true), { safe: text, redactions: 0 })
})

test('masks third-party message bodies only in strict cloud mode', () => {
  assert.equal(maskMessageBodyText('私密消息', 'strict', false), '[内容4字已按严格模式屏蔽]')
  assert.equal(maskMessageBodyText('私密消息', 'balanced', false), '私密消息')
  assert.equal(maskMessageBodyText('私密消息', 'strict', true), '私密消息')
})

test('rejects MCP path traversal and invalid dates', () => {
  const root = join(tmpdir(), 'weflow-test-root')
  assert.equal(safeChildPath(root, 'article.md'), join(root, 'article.md'))
  assert.equal(safeChildPath(root, '../secret.txt'), null)
  assert.equal(safeChildPath(root, root), null)
  assert.equal(safeDate('2026-08-27'), '2026-08-27')
  assert.equal(safeDate('2026-8-27'), null)
  assert.equal(safeDate('../secret'), null)
})

test('accepts real image signatures and rejects non-images', () => {
  const dir = mkdtempSync(join(tmpdir(), 'weflow-cover-'))
  const png = join(dir, 'cover.bin')
  const text = join(dir, 'text.bin')
  writeFileSync(png, Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
  writeFileSync(text, 'not an image')
  assert.equal(isCoverImage(png), true)
  assert.equal(isCoverImage(text), false)
  assert.equal(isCoverImage(join(dir, 'missing.png')), false)
})
